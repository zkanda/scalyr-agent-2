# Copyright 2014-2020 Scalyr Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import unicode_literals
from __future__ import print_function
from __future__ import absolute_import

if False:  # NOSONAR
    from typing import Dict
    from typing import Any

import time
import os

import pytest

from scalyr_agent.third_party import pymysql

from tests.utils.agent_runner import AgentRunner

from tests.utils.dockerized import dockerized_case
from tests.image_builder.monitors.common import CommonMonitorBuilder
from tests.utils.log_reader import LogMetricReader
from tests.utils.log_reader import AgentLogReader

import six

HOST = "localhost"
USERNAME = "scalyr_test_user"
PASSWORD = "scalyr_test_password"
DATABASE = "scalyr_test_db"


@pytest.fixture()
def mysql_client():
    # we change owner of the mysql files to workaround the issue which happens with mysql server in docker.
    # see: https://serverfault.com/a/872576
    os.system("chown -R mysql:mysql /var/lib/mysql /var/run/mysqld")

    exit_code = os.system("service mysql start")

    # On failure include service logs for ease of debugging
    if exit_code != 0:
        os.system("cat /var/log/mysql/mysql.log || true")

    os.system("mysql < /init.sql")

    time.sleep(3)

    client = pymysql.connect(host=HOST, user=USERNAME, password=PASSWORD, db=DATABASE)

    yield client
    client.close()


@pytest.fixture()
def mysql_cursor(mysql_client):
    cursor = mysql_client.cursor()

    yield cursor

    cursor.close()


class MysqlAgentRunner(AgentRunner):
    def __init__(self):
        super(MysqlAgentRunner, self).__init__(
            enable_coverage=True, send_to_server=False
        )

        self.mysql_log_path = self.add_log_file(
            self.agent_logs_dir_path / "mysql_monitor.log"
        )

    @property
    def _agent_config(self):  # type: () -> Dict[six.text_type, Any]
        config = super(MysqlAgentRunner, self)._agent_config
        config["monitors"].append(
            {
                "module": "scalyr_agent.builtin_monitors.mysql_monitor",
                "id": "instance1",
                "database_socket": "default",
                "database_username": USERNAME,
                "database_password": PASSWORD,
            }
        )

        return config


class MySqlLogReader(LogMetricReader):
    LINE_PATTERN = r"\s*(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}.\d+Z)\s\[mysql_monitor\((?P<instance_id>[^\]]+)\)\]\s(?P<metric_name>[^\s]+)\s(?P<metric_value>.+)"


def _test(request, python_version):
    mysql_cursor = request.getfixturevalue("mysql_cursor")

    runner = MysqlAgentRunner()

    runner.start(executable=python_version)

    time.sleep(1)
    mysql_cursor.execute(
        "CREATE TABLE test_table( id INT AUTO_INCREMENT PRIMARY KEY, text VARCHAR(255));"
    )

    reader = MySqlLogReader(runner.mysql_log_path)
    agent_log_reader = AgentLogReader(runner.agent_log_file_path)

    agent_log_reader.wait(5)

    metrics_to_check = [
        "mysql.global.com_insert",
        "mysql.global.com_select",
        "mysql.global.com_delete",
        "mysql.global.com_update",
    ]

    reader.wait_for_metrics_exist(metrics_to_check, timeout=60)

    reader.wait_for_metrics_equal(
        expected={
            "mysql.global.com_insert": 0,
            "mysql.global.com_select": 2,
            "mysql.global.com_delete": 0,
            "mysql.global.com_update": 0,
        },
        timeout=60,
    )

    rows = mysql_cursor.execute("SELECT * FROM test_table")

    assert rows == 0

    mysql_cursor.execute('INSERT INTO test_table (text) values ("row1")')
    mysql_cursor.execute("SELECT LAST_INSERT_ID()")
    (row1_id,) = mysql_cursor.fetchone()

    rows = mysql_cursor.execute("SELECT * FROM test_table")

    assert rows == 1

    mysql_cursor.execute('INSERT INTO test_table (text) values ("row2")')
    mysql_cursor.execute("SELECT LAST_INSERT_ID()")
    (row2_id,) = mysql_cursor.fetchone()

    rows = mysql_cursor.execute("SELECT * FROM test_table")

    assert rows == 2

    mysql_cursor.execute("DELETE FROM test_table WHERE id={0};".format(row2_id))

    rows = mysql_cursor.execute("SELECT * FROM test_table;")

    assert rows == 1

    mysql_cursor.execute(
        'UPDATE test_table SET text="updated_row1" WHERE id={0};'.format(row1_id)
    )

    mysql_cursor.execute("SELECT text FROM test_table WHERE id={0}".format(row1_id))

    (row1_text,) = mysql_cursor.fetchone()
    assert row1_text == "updated_row1"

    reader.wait_for_metrics_equal(
        expected={
            "mysql.global.com_insert": 2,
            "mysql.global.com_select": 9,
            "mysql.global.com_delete": 1,
            "mysql.global.com_update": 1,
        },
        timeout=60,
    )

    agent_log_reader.go_to_end()


@pytest.mark.usefixtures("agent_environment")
@dockerized_case(CommonMonitorBuilder, __file__)
def test_mysql_python2(request):
    _test(request, python_version="python2")


@pytest.mark.usefixtures("agent_environment")
@dockerized_case(CommonMonitorBuilder, __file__)
def test_mysql_python3(request):
    _test(request, python_version="python3")
