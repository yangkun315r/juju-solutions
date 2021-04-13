#!/usr/bin/env python3

import os
import time
import unittest

import yaml
import amulet


class TestBundle(unittest.TestCase):
    bundle_file = os.path.join(os.path.dirname(__file__), '..', 'bundle.yaml')

    @classmethod
    def setUpClass(cls):
        cls.d = amulet.Deployment(series='trusty')
        with open(cls.bundle_file) as f:
            bun = f.read()
        bundle = yaml.safe_load(bun)
        cls.d.load(bundle)
        cls.d.setup(timeout=1800)
        cls.d.sentry.wait_for_messages({'zeppelin': 'Ready'}, timeout=1800)
        cls.hdfs = cls.d.sentry['hdfs-master'][0]
        cls.yarn = cls.d.sentry['yarn-master'][0]
        cls.slave = cls.d.sentry['compute-slave'][0]
        cls.secondary = cls.d.sentry['secondary-namenode'][0]
        cls.spark = cls.d.sentry['spark'][0]
        cls.zeppelin = cls.d.sentry['zeppelin'][0]

    @classmethod
    def tearDownClass(cls):
        # classmethod inheritance doesn't work quite right with
        # setUpClass / tearDownClass, so subclasses have to manually call this
        juju_env = amulet.helpers.default_environment()
        services = ['hdfs-master', 'yarn-master', 'compute-slave', 'secondary-namenode', 'plugin', 'spark', 'zeppelin']

        def check_env_clear():
            state = amulet.waiter.state(juju_env=juju_env)
            for service in services:
                if state.get(service, {}) != {}:
                    return False
            return True

        for service in services:
            cls.d.remove(service)
        for i in amulet.helpers.timeout_gen(900):
            if check_env_clear():
                break
            time.sleep(5)

    def test_components(self):
        """
        Confirm that all of the required components are up and running.
        """
        hdfs, retcode = self.hdfs.run("pgrep -a java")
        yarn, retcode = self.yarn.run("pgrep -a java")
        slave, retcode = self.slave.run("pgrep -a java")
        secondary, retcode = self.secondary.run("pgrep -a java")
        spark, retcode = self.spark.run("pgrep -a java")

        # .NameNode needs the . to differentiate it from SecondaryNameNode
        assert '.NameNode' in hdfs, "NameNode not started"
        assert '.NameNode' not in yarn, "NameNode should not be running on yarn-master"
        assert '.NameNode' not in slave, "NameNode should not be running on compute-slave"
        assert '.NameNode' not in secondary, "NameNode should not be running on secondary-namenode"
        assert '.NameNode' not in spark, "NameNode should not be running on spark"

        assert 'ResourceManager' in yarn, "ResourceManager not started"
        assert 'ResourceManager' not in hdfs, "ResourceManager should not be running on hdfs-master"
        assert 'ResourceManager' not in slave, "ResourceManager should not be running on compute-slave"
        assert 'ResourceManager' not in secondary, "ResourceManager should not be running on secondary-namenode"
        assert 'ResourceManager' not in spark, "ResourceManager should not be running on spark"

        assert 'JobHistoryServer' in yarn, "JobHistoryServer not started"
        assert 'JobHistoryServer' not in hdfs, "JobHistoryServer should not be running on hdfs-master"
        assert 'JobHistoryServer' not in slave, "JobHistoryServer should not be running on compute-slave"
        assert 'JobHistoryServer' not in secondary, "JobHistoryServer should not be running on secondary-namenode"
        assert 'JobHistoryServer' not in spark, "JobHistoryServer should not be running on spark"

        assert 'NodeManager' in slave, "NodeManager not started"
        assert 'NodeManager' not in yarn, "NodeManager should not be running on yarn-master"
        assert 'NodeManager' not in hdfs, "NodeManager should not be running on hdfs-master"
        assert 'NodeManager' not in secondary, "NodeManager should not be running on secondary-namenode"
        assert 'NodeManager' not in spark, "NodeManager should not be running on spark"

        assert 'DataNode' in slave, "DataServer not started"
        assert 'DataNode' not in yarn, "DataNode should not be running on yarn-master"
        assert 'DataNode' not in hdfs, "DataNode should not be running on hdfs-master"
        assert 'DataNode' not in secondary, "DataNode should not be running on secondary-namenode"
        assert 'DataNode' not in spark, "DataNode should not be running on spark"

        assert 'SecondaryNameNode' in secondary, "SecondaryNameNode not started"
        assert 'SecondaryNameNode' not in yarn, "SecondaryNameNode should not be running on yarn-master"
        assert 'SecondaryNameNode' not in hdfs, "SecondaryNameNode should not be running on hdfs-master"
        assert 'SecondaryNameNode' not in slave, "SecondaryNameNode should not be running on compute-slave"
        assert 'SecondaryNameNode' not in spark, "SecondaryNameNode should not be running on spark"

        assert 'spark' in spark, 'Spark should be running on spark'
        assert 'zeppelin' in spark, 'Zeppelin should be running on spark'

    def test_hdfs_dir(self):
        """
        Validate admin few hadoop activities on HDFS cluster.
            1) This test validates mkdir on hdfs cluster
            2) This test validates change hdfs dir owner on the cluster
            3) This test validates setting hdfs directory access permission on the cluster

        NB: These are order-dependent, so must be done as part of a single test case.
        """
        output, retcode = self.spark.run("su hdfs -c 'hdfs dfs -mkdir -p /user/ubuntu'")
        assert retcode == 0, "Created a user directory on hdfs FAILED:\n{}".format(output)
        output, retcode = self.spark.run("su hdfs -c 'hdfs dfs -chown ubuntu:ubuntu /user/ubuntu'")
        assert retcode == 0, "Assigning an owner to hdfs directory FAILED:\n{}".format(output)
        output, retcode = self.spark.run("su hdfs -c 'hdfs dfs -chmod -R 755 /user/ubuntu'")
        assert retcode == 0, "seting directory permission on hdfs FAILED:\n{}".format(output)

    def test_yarn_mapreduce_exe(self):
        """
        Validate yarn mapreduce operations:
            1) validate mapreduce execution - writing to hdfs
            2) validate successful mapreduce operation after the execution
            3) validate mapreduce execution - reading and writing to hdfs
            4) validate successful mapreduce operation after the execution
            5) validate successful deletion of mapreduce operation result from hdfs

        NB: These are order-dependent, so must be done as part of a single test case.
        """
        jar_file = '/usr/lib/hadoop/share/hadoop/mapreduce/hadoop-mapreduce-examples-*.jar'
        test_steps = [
            ('teragen',      "su ubuntu -c 'hadoop jar {} teragen  10000 /user/ubuntu/teragenout'".format(jar_file)),
            ('mapreduce #1', "su hdfs -c 'hdfs dfs -ls /user/ubuntu/teragenout/_SUCCESS'"),
            ('terasort',     "su ubuntu -c 'hadoop jar {} terasort /user/ubuntu/teragenout /user/ubuntu/terasortout'".
                format(jar_file)),
            ('mapreduce #2', "su hdfs -c 'hdfs dfs -ls /user/ubuntu/terasortout/_SUCCESS'"),
            ('cleanup',      "su hdfs -c 'hdfs dfs -rm -r /user/ubuntu/teragenout'"),
        ]
        for name, step in test_steps:
            output, retcode = self.spark.run(step)
            assert retcode == 0, "{} FAILED:\n{}".format(name, output)

    def test_spark(self):
        output, retcode = self.spark.run("su ubuntu -c 'bash -lc /home/ubuntu/sparkpi.sh 2>&1'")
        assert 'Pi is roughly' in output, 'SparkPI test failed: %s' % output

    def test_zeppelin(self):
        pass  # requires javascript; how to test?


if __name__ == '__main__':
    unittest.main()
