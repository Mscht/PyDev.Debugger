# coding: utf-8
'''
    The idea is that we record the commands sent to the debugger and reproduce them from this script
    (so, this works as the client, which spawns the debugger as a separate process and communicates
    to it as if it was run from the outside)

    Note that it's a python script but it'll spawn a process to run as jython, ironpython and as python.
'''
import os
import platform
import sys
import threading
import time
import unittest

import pytest

from tests_python import debugger_unittest
from tests_python.debugger_unittest import (get_free_port, CMD_SET_PROPERTY_TRACE, REASON_CAUGHT_EXCEPTION,
    REASON_UNCAUGHT_EXCEPTION, REASON_STOP_ON_BREAKPOINT, REASON_THREAD_SUSPEND, overrides, CMD_THREAD_CREATE,
    CMD_GET_THREAD_STACK, REASON_STEP_INTO_MY_CODE, CMD_GET_EXCEPTION_DETAILS)
from _pydevd_bundle.pydevd_constants import IS_WINDOWS
try:
    from urllib import unquote
except ImportError:
    from urllib.parse import unquote

IS_CPYTHON = platform.python_implementation() == 'CPython'
IS_IRONPYTHON = platform.python_implementation() == 'IronPython'
IS_JYTHON = platform.python_implementation() == 'Jython'
IS_APPVEYOR = os.environ.get('APPVEYOR', '') in ('True', 'true', '1')

try:
    xrange
except:
    xrange = range

TEST_DJANGO = False
if sys.version_info[:2] == (2, 7):
    # Only test on python 2.7 for now
    try:
        import django
        TEST_DJANGO = True
    except:
        pass

IS_PY2 = False
if sys.version_info[0] == 2:
    IS_PY2 = True

IS_PY26 = sys.version_info[:2] == (2, 6)

if IS_PY2:
    builtin_qualifier = "__builtin__"
else:
    builtin_qualifier = "builtins"

IS_PY36 = False
if sys.version_info[0] == 3 and sys.version_info[1] == 6:
    IS_PY36 = True

from tests_python.debug_constants import TEST_CYTHON
from tests_python.debug_constants import TEST_JYTHON


#=======================================================================================================================
# AbstractRemoteWriterThread
#=======================================================================================================================
class AbstractRemoteWriterThread(debugger_unittest.AbstractWriterThread):

    def update_command_line_args(self, args):
        ret = debugger_unittest.AbstractWriterThread.update_command_line_args(self, args)
        ret.append(str(self.port))
        return ret


#=======================================================================================================================
# WriterThreadCaseRemoteDebugger
#=======================================================================================================================
class WriterThreadCaseRemoteDebugger(AbstractRemoteWriterThread):

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_remote.py')

    def run(self):
        self.start_socket()

        self.log.append('making initial run')
        self.write_make_initial_run()

        self.log.append('waiting for breakpoint hit')
        hit = self.wait_for_breakpoint_hit(REASON_THREAD_SUSPEND)

        self.log.append('run thread')
        self.write_run_thread(hit.thread_id)

        self.log.append('asserting')
        try:
            assert 5 == self._sequence, 'Expected 5. Had: %s' % self._sequence
        except:
            self.log.append('assert failed!')
            raise
        self.log.append('asserted')

        self.finished_ok = True


#=======================================================================================================================
# WriterThreadCaseRemoteDebuggerUnhandledExceptions
#=======================================================================================================================
class WriterThreadCaseRemoteDebuggerUnhandledExceptions(AbstractRemoteWriterThread):

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_remote_unhandled_exceptions.py')

    @overrides(AbstractRemoteWriterThread.check_test_suceeded_msg)
    def check_test_suceeded_msg(self, stdout, stderr):
        return 'TEST SUCEEDED' in ''.join(stderr)

    @overrides(AbstractRemoteWriterThread.additional_output_checks)
    def additional_output_checks(self, stdout, stderr):
        # Don't call super as we have an expected exception
        assert 'ValueError: TEST SUCEEDED' in stderr

    def run(self):
        self.start_socket()  # Wait for it to connect back at this port.

        self.log.append('making initial run')
        self.write_make_initial_run()

        self.log.append('waiting for breakpoint hit')
        hit = self.wait_for_breakpoint_hit(REASON_THREAD_SUSPEND)

        self.write_add_exception_breakpoint_with_policy('Exception', '0', '1', '0')

        self.log.append('run thread')
        self.write_run_thread(hit.thread_id)

        self.log.append('waiting for uncaught exception')
        hit = self.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
        self.write_run_thread(hit.thread_id)

        self.log.append('finished ok')
        self.finished_ok = True


#=======================================================================================================================
# WriterThreadCaseRemoteDebuggerUnhandledExceptions2
#=======================================================================================================================
class WriterThreadCaseRemoteDebuggerUnhandledExceptions2(AbstractRemoteWriterThread):

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_remote_unhandled_exceptions2.py')

    @overrides(AbstractRemoteWriterThread.check_test_suceeded_msg)
    def check_test_suceeded_msg(self, stdout, stderr):
        return 'TEST SUCEEDED' in ''.join(stderr)

    @overrides(AbstractRemoteWriterThread.additional_output_checks)
    def additional_output_checks(self, stdout, stderr):
        # Don't call super as we have an expected exception
        assert 'ValueError: TEST SUCEEDED' in stderr

    def run(self):
        self.start_socket()  # Wait for it to connect back at this port.

        self.log.append('making initial run')
        self.write_make_initial_run()

        self.log.append('waiting for breakpoint hit')
        hit = self.wait_for_breakpoint_hit(REASON_THREAD_SUSPEND)

        self.write_add_exception_breakpoint_with_policy('ValueError', '0', '1', '0')

        self.log.append('run thread')
        self.write_run_thread(hit.thread_id)

        self.log.append('waiting for uncaught exception')
        for _ in range(3):
            # Note: this isn't ideal, but in the remote attach case, if the
            # exception is raised at the topmost frame, we consider the exception to
            # be an uncaught exception even if it'll be handled at that point.
            # See: https://github.com/Microsoft/ptvsd/issues/580
            # To properly fix this, we'll need to identify that this exception
            # will be handled later on with the information we have at hand (so,
            # no back frame but within a try..except block).
            hit = self.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
            self.write_run_thread(hit.thread_id)

        self.log.append('finished ok')
        self.finished_ok = True


#=======================================================================================================================
# _SecondaryMultiProcProcessWriterThread
#=======================================================================================================================
class _SecondaryMultiProcProcessWriterThread(debugger_unittest.AbstractWriterThread):

    FORCE_KILL_PROCESS_WHEN_FINISHED_OK = True

    def __init__(self, server_socket):
        debugger_unittest.AbstractWriterThread.__init__(self)
        self.server_socket = server_socket

    def run(self):
        print('waiting for second process')
        self.sock, addr = self.server_socket.accept()
        print('accepted second process')

        from tests_python.debugger_unittest import ReaderThread
        self.reader_thread = ReaderThread(self.sock)
        self.reader_thread.start()

        self._sequence = -1
        # initial command is always the version
        self.write_version()
        self.log.append('start_socket')
        self.write_make_initial_run()
        time.sleep(.5)
        self.finished_ok = True


#=======================================================================================================================
# WriterThreadCaseRemoteDebuggerMultiProc
#=======================================================================================================================
class WriterThreadCaseRemoteDebuggerMultiProc(AbstractRemoteWriterThread):

    # It seems sometimes it becomes flaky on the ci because the process outlives the writer thread...
    # As we're only interested in knowing if a second connection was received, just kill the related
    # process.
    FORCE_KILL_PROCESS_WHEN_FINISHED_OK = True

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_remote_1.py')

    def run(self):
        self.start_socket()

        self.log.append('making initial run')
        self.write_make_initial_run()

        self.log.append('waiting for breakpoint hit')
        hit = self.wait_for_breakpoint_hit(REASON_THREAD_SUSPEND)

        self.secondary_multi_proc_process_writer_thread = secondary_multi_proc_process_writer_thread = \
            _SecondaryMultiProcProcessWriterThread(self.server_socket)
        secondary_multi_proc_process_writer_thread.start()

        self.log.append('run thread')
        self.write_run_thread(hit.thread_id)

        for _i in xrange(400):
            if secondary_multi_proc_process_writer_thread.finished_ok:
                break
            time.sleep(.1)
        else:
            self.log.append('Secondary process not finished ok!')
            raise AssertionError('Secondary process not finished ok!')

        self.log.append('Secondary process finished!')
        try:
            assert 5 == self._sequence, 'Expected 5. Had: %s' % self._sequence
        except:
            self.log.append('assert failed!')
            raise
        self.log.append('asserted')

        self.finished_ok = True

    def do_kill(self):
        debugger_unittest.AbstractWriterThread.do_kill(self)
        if hasattr(self, 'secondary_multi_proc_process_writer_thread'):
            self.secondary_multi_proc_process_writer_thread.do_kill()


#=======================================================================================================================
# WriterDebugZipFiles
#======================================================================================================================
class WriterDebugZipFiles(debugger_unittest.AbstractWriterThread):

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_zip_files.py')

    def __init__(self, tmpdir):
        self.tmpdir = tmpdir
        super(WriterDebugZipFiles, self).__init__()
        import zipfile
        zip_file = zipfile.ZipFile(
            str(tmpdir.join('myzip.zip')), 'w')
        zip_file.writestr('zipped/__init__.py', '')
        zip_file.writestr('zipped/zipped_contents.py', 'def call_in_zip():\n    return 1')
        zip_file.close()

        zip_file = zipfile.ZipFile(
            str(tmpdir.join('myzip2.egg!')), 'w')
        zip_file.writestr('zipped2/__init__.py', '')
        zip_file.writestr('zipped2/zipped_contents2.py', 'def call_in_zip2():\n    return 1')
        zip_file.close()

    @overrides(debugger_unittest.AbstractWriterThread.get_environ)
    def get_environ(self):
        env = os.environ.copy()
        curr_pythonpath = env.get('PYTHONPATH', '')

        curr_pythonpath = str(self.tmpdir.join('myzip.zip')) + os.pathsep + curr_pythonpath
        curr_pythonpath = str(self.tmpdir.join('myzip2.egg!')) + os.pathsep + curr_pythonpath
        env['PYTHONPATH'] = curr_pythonpath

        env["IDE_PROJECT_ROOTS"] = str(self.tmpdir.join('myzip.zip'))
        return env

    def run(self):
        self.start_socket()
        self.write_add_breakpoint(
            2,
            'None',
            filename=os.path.join(str(self.tmpdir.join('myzip.zip')), 'zipped', 'zipped_contents.py')
        )

        self.write_add_breakpoint(
            2,
            'None',
            filename=os.path.join(str(self.tmpdir.join('myzip2.egg!')), 'zipped2', 'zipped_contents2.py')
        )

        self.write_make_initial_run()
        hit = self.wait_for_breakpoint_hit()
        assert hit.name == 'call_in_zip'
        self.write_run_thread(hit.thread_id)

        hit = self.wait_for_breakpoint_hit()
        assert hit.name == 'call_in_zip2'
        self.write_run_thread(hit.thread_id)

        self.finished_ok = True


#=======================================================================================================================
# WriterCaseBreakpointSuspensionPolicy
#======================================================================================================================
class WriterCaseBreakpointSuspensionPolicy(debugger_unittest.AbstractWriterThread):

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_suspend_policy.py')

    def run(self):
        self.start_socket()
        self.write_add_breakpoint(25, '', filename=self.TEST_FILE, hit_condition='', is_logpoint=False, suspend_policy='ALL')
        self.write_make_initial_run()

        thread_ids = []
        for i in range(3):
            self.log.append('Waiting for thread %s of 3 to stop' % (i + 1,))
            # One thread is suspended with a breakpoint hit and the other 2 as thread suspended.
            hit = self.wait_for_breakpoint_hit((REASON_STOP_ON_BREAKPOINT, REASON_THREAD_SUSPEND))
            thread_ids.append(hit.thread_id)

        for thread_id in thread_ids:
            self.write_run_thread(thread_id)

        self.finished_ok = True


#=======================================================================================================================
# WriterCaseGetThreadStack
#======================================================================================================================
class WriterCaseGetThreadStack(debugger_unittest.AbstractWriterThread):

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_get_thread_stack.py')

    def _ignore_stderr_line(self, line):
        if debugger_unittest.AbstractWriterThread._ignore_stderr_line(self, line):
            return True

        if IS_JYTHON:
            for expected in (
                "RuntimeWarning: Parent module '_pydev_bundle' not found while handling absolute import",
                "from java.lang import System"):
                if expected in line:
                    return True

        return False

    def run(self):
        self.start_socket()
        self.write_add_breakpoint(18, None)
        self.write_make_initial_run()

        thread_created_msgs = [self.wait_for_message(lambda msg:msg.startswith('%s\t' % (CMD_THREAD_CREATE,)))]
        thread_created_msgs.append(self.wait_for_message(lambda msg:msg.startswith('%s\t' % (CMD_THREAD_CREATE,))))
        thread_id_to_name = {}
        for msg in thread_created_msgs:
            thread_id_to_name[msg.thread['id']] = msg.thread['name']
        assert len(thread_id_to_name) == 2

        hit = self.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT)
        assert hit.thread_id in thread_id_to_name

        for request_thread_id in thread_id_to_name:
            self.write_get_thread_stack(request_thread_id)
            msg = self.wait_for_message(lambda msg:msg.startswith('%s\t' % (CMD_GET_THREAD_STACK,)))
            files = [frame['file'] for frame in  msg.thread.frame]
            assert msg.thread['id'] == request_thread_id
            if not files[0].endswith('_debugger_case_get_thread_stack.py'):
                raise AssertionError('Expected to find _debugger_case_get_thread_stack.py in files[0]. Found: %s' % ('\n'.join(files),))

            if ([filename for filename in files if filename.endswith('pydevd.py')]):
                raise AssertionError('Did not expect to find pydevd.py. Found: %s' % ('\n'.join(files),))
            if request_thread_id == hit.thread_id:
                assert len(msg.thread.frame) == 0  # In main thread (must have no back frames).
                assert msg.thread.frame['name'] == '<module>'
            else:
                assert len(msg.thread.frame) > 1  # Stopped in threading (must have back frames).
                assert msg.thread.frame[0]['name'] == 'method'

        self.write_run_thread(hit.thread_id)

        self.finished_ok = True


#=======================================================================================================================
# WriterCaseDumpThreadsToStderr
#======================================================================================================================
class WriterCaseDumpThreadsToStderr(debugger_unittest.AbstractWriterThread):

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_get_thread_stack.py')

    def additional_output_checks(self, stdout, stderr):
        assert 'Thread Dump' in stderr and 'Thread pydevd.CommandThread  (daemon: True, pydevd thread: True)' in stderr, \
            'Did not find thread dump in stderr. stderr:\n%s' % (stderr,)

    def run(self):
        self.start_socket()
        self.write_add_breakpoint(12, None)
        self.write_make_initial_run()

        hit = self.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT)

        self.write_dump_threads()
        self.write_run_thread(hit.thread_id)

        self.finished_ok = True


#=======================================================================================================================
# WriterCaseStopOnStartRegular
#=======================================================================================================================
class WriterCaseStopOnStartRegular(debugger_unittest.AbstractWriterThread):

    TEST_FILE = debugger_unittest._get_debugger_test_file('_debugger_case_simple_calls.py')

    def run(self):
        self.start_socket()
        self.write_stop_on_start()
        self.write_make_initial_run()

        hit = self.wait_for_breakpoint_hit(REASON_STEP_INTO_MY_CODE, file='_debugger_case_simple_calls.py', line=1)

        self.write_run_thread(hit.thread_id)

        self.finished_ok = True

# # ======================================================================================================================
# # WriterCaseStopOnStartMSwitch
# # ======================================================================================================================
# class WriterCaseStopOnStartMSwitch(WriterThreadCaseMSwitch):
#
#     def run(self):
#         self.start_socket()
#         self.write_stop_on_start()
#         self.write_make_initial_run()
#
#         hit = self.wait_for_breakpoint_hit(REASON_STEP_INTO_MY_CODE, file='_debugger_case_m_switch.py', line=1)
#
#         self.write_run_thread(hit.thread_id)
#
#         self.finished_ok = True
#
#
# # ======================================================================================================================
# # WriterCaseStopOnStartEntryPoint
# # ======================================================================================================================
# class WriterCaseStopOnStartEntryPoint(WriterThreadCaseModuleWithEntryPoint):
#
#     def run(self):
#         self.start_socket()
#         self.write_stop_on_start()
#         self.write_make_initial_run()
#
#         hit = self.wait_for_breakpoint_hit(REASON_STEP_INTO_MY_CODE, file='_debugger_case_module_entry_point.py', line=1)
#
#         self.write_run_thread(hit.thread_id)
#
#         self.finished_ok = True


class AbstractWriterThreadCaseDjango(debugger_unittest.AbstractWriterThread):

    FORCE_KILL_PROCESS_WHEN_FINISHED_OK = True

    def _ignore_stderr_line(self, line):
        if debugger_unittest.AbstractWriterThread._ignore_stderr_line(self, line):
            return True

        if 'GET /my_app' in line:
            return True

        return False

    def get_command_line_args(self):
        free_port = get_free_port()
        self.django_port = free_port
        return [
            debugger_unittest._get_debugger_test_file(os.path.join('my_django_proj_17', 'manage.py')),
            'runserver',
            '--noreload',
            str(free_port),
        ]

    def write_add_breakpoint_django(self, line, func, template):
        '''
            @param line: starts at 1
        '''
        breakpoint_id = self.next_breakpoint_id()
        template_file = debugger_unittest._get_debugger_test_file(os.path.join('my_django_proj_17', 'my_app', 'templates', 'my_app', template))
        self.write("111\t%s\t%s\t%s\t%s\t%s\t%s\tNone\tNone" % (self.next_seq(), breakpoint_id, 'django-line', template_file, line, func))
        self.log.append('write_add_django_breakpoint: %s line: %s func: %s' % (breakpoint_id, line, func))
        return breakpoint_id

    def create_request_thread(self, uri):
        outer = self

        class T(threading.Thread):

            def run(self):
                try:
                    from urllib.request import urlopen
                except ImportError:
                    from urllib import urlopen
                for _ in xrange(10):
                    try:
                        stream = urlopen('http://127.0.0.1:%s/%s' % (outer.django_port, uri))
                        self.contents = stream.read()
                        break
                    except IOError:
                        continue

        return T()


class DebuggerRunnerSimple(debugger_unittest.DebuggerRunner):

    def get_command_line(self):
        if IS_JYTHON:
            if sys.executable is not None:
                # i.e.: we're running with the provided jython.exe
                return [sys.executable]
            else:

                return [
                    get_java_location(),
                    '-classpath',
                    get_jython_jar(),
                    'org.python.util.jython'
                ]

        if IS_CPYTHON:
            return [sys.executable, '-u']

        if IS_IRONPYTHON:
            return [
                    sys.executable,
                    '-X:Frames'
                ]

        raise RuntimeError('Unable to provide command line')


@pytest.fixture
def case_setup():

    from contextlib import contextmanager

    runner = DebuggerRunnerSimple()

    class WriterThread(debugger_unittest.AbstractWriterThread):

        TEST_FILE = None

        def run(self):
            self.start_socket()
            while not self.finished_ok:
                time.sleep(.05)

    class CaseSetup(object):

        @contextmanager
        def test_file(
                self,
                filename,
                **kwargs
            ):
            WriterThread.TEST_FILE = debugger_unittest._get_debugger_test_file(filename)
            for key, value in kwargs.items():
                assert hasattr(WriterThread, key)
                setattr(WriterThread, key, value)

            with runner.check_case(WriterThread) as writer_thread:
                yield writer_thread

    return CaseSetup()


class WriterThreadCaseMSwitch(debugger_unittest.AbstractWriterThread):

    TEST_FILE = 'tests_python.resources._debugger_case_m_switch'
    IS_MODULE = True

    @overrides(debugger_unittest.AbstractWriterThread.get_environ)
    def get_environ(self):
        env = os.environ.copy()
        curr_pythonpath = env.get('PYTHONPATH', '')

        root_dirname = os.path.dirname(os.path.dirname(__file__))

        curr_pythonpath += root_dirname + os.pathsep
        env['PYTHONPATH'] = curr_pythonpath
        return env

    @overrides(debugger_unittest.AbstractWriterThread.get_main_filename)
    def get_main_filename(self):
        return debugger_unittest._get_debugger_test_file('_debugger_case_m_switch.py')


class WriterThreadCaseModuleWithEntryPoint(WriterThreadCaseMSwitch):

    TEST_FILE = 'tests_python.resources._debugger_case_module_entry_point:main'
    IS_MODULE = True

    @overrides(WriterThreadCaseMSwitch.get_main_filename)
    def get_main_filename(self):
        return debugger_unittest._get_debugger_test_file('_debugger_case_module_entry_point.py')


@pytest.fixture
def case_setup_m_switch():

    from contextlib import contextmanager

    runner = DebuggerRunnerSimple()

    class WriterThread(WriterThreadCaseMSwitch):

        def run(self):
            self.start_socket()
            while not self.finished_ok:
                time.sleep(.05)

    class CaseSetup(object):

        @contextmanager
        def test_file(self):
            with runner.check_case(WriterThread) as writer_thread:
                yield writer_thread

    return CaseSetup()


@pytest.fixture
def case_setup_m_switch_entry_point():

    from contextlib import contextmanager

    runner = DebuggerRunnerSimple()

    class WriterThread(WriterThreadCaseModuleWithEntryPoint):

        def run(self):
            self.start_socket()
            while not self.finished_ok:
                time.sleep(.05)

    class CaseSetup(object):

        @contextmanager
        def test_file(self):
            with runner.check_case(WriterThread) as writer_thread:
                yield writer_thread

    return CaseSetup()


@pytest.fixture
def case_setup_django():

    from contextlib import contextmanager

    runner = DebuggerRunnerSimple()

    class WriterThread(AbstractWriterThreadCaseDjango):

        TEST_FILE = None

        def run(self):
            self.start_socket()
            while not self.finished_ok:
                time.sleep(.05)

    class CaseSetup(object):

        @contextmanager
        def test_file(self, filename):
            WriterThread.TEST_FILE = debugger_unittest._get_debugger_test_file(filename)
            with runner.check_case(WriterThread) as writer_thread:
                yield writer_thread

    return CaseSetup()


@pytest.mark.skipif(IS_IRONPYTHON, reason='Test needs gc.get_referrers to really check anything.')
def test_case_1(case_setup):
    with case_setup.test_file('_debugger_case1.py') as writer_thread:
        writer_thread.log.append('writing add breakpoint')
        writer_thread.write_add_breakpoint(6, 'set_up')

        writer_thread.log.append('making initial run')
        writer_thread.write_make_initial_run()

        writer_thread.log.append('waiting for breakpoint hit')
        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.log.append('get frame')
        writer_thread.write_get_frame(thread_id, frame_id)

        writer_thread.log.append('step over')
        writer_thread.write_step_over(thread_id)

        writer_thread.log.append('get frame')
        writer_thread.write_get_frame(thread_id, frame_id)

        writer_thread.log.append('run thread')
        writer_thread.write_run_thread(thread_id)

        writer_thread.log.append('asserting')
        try:
            assert 13 == writer_thread._sequence, 'Expected 13. Had: %s' % writer_thread._sequence
        except:
            writer_thread.log.append('assert failed!')
            raise
        writer_thread.log.append('asserted')

        writer_thread.finished_ok = True


def test_case_2(case_setup):
    with case_setup.test_file('_debugger_case2.py') as writer_thread:
        writer_thread.write_add_breakpoint(3, 'Call4')  # seq = 3
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_get_frame(thread_id, frame_id)  # Note: write get frame but not waiting for it to be gotten.

        writer_thread.write_add_breakpoint(14, 'Call2')

        writer_thread.write_run_thread(thread_id)

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_get_frame(thread_id, frame_id)  # Note: write get frame but not waiting for it to be gotten.

        writer_thread.write_run_thread(thread_id)

        writer_thread.log.append('Checking sequence. Found: %s' % (writer_thread._sequence))
        assert 15 == writer_thread._sequence, 'Expected 15. Had: %s' % writer_thread._sequence

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_IRONPYTHON, reason='This test fails once in a while due to timing issues on IronPython, so, skipping it.')
def test_case_3(case_setup):
    with case_setup.test_file('_debugger_case3.py') as writer_thread:
        writer_thread.write_make_initial_run()
        time.sleep(.5)
        breakpoint_id = writer_thread.write_add_breakpoint(4, '')
        writer_thread.write_add_breakpoint(5, 'FuncNotAvailable')  # Check that it doesn't get hit in the global when a function is available

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_get_frame(thread_id, frame_id)

        writer_thread.write_run_thread(thread_id)

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_get_frame(thread_id, frame_id)

        writer_thread.write_remove_breakpoint(breakpoint_id)

        writer_thread.write_run_thread(thread_id)

        assert 17 == writer_thread._sequence, 'Expected 17. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='This test is flaky on Jython, so, skipping it.')
def test_case_4(case_setup):
    with case_setup.test_file('_debugger_case4.py') as writer_thread:
        writer_thread.write_make_initial_run()

        thread_id = writer_thread.wait_for_new_thread()

        writer_thread.write_suspend_thread(thread_id)

        hit = writer_thread.wait_for_breakpoint_hit(REASON_THREAD_SUSPEND)
        assert hit.thread_id == thread_id

        writer_thread.write_run_thread(thread_id)

        writer_thread.finished_ok = True


def test_case_5(case_setup):
    with case_setup.test_file('_debugger_case56.py') as writer_thread:
        breakpoint_id = writer_thread.write_add_breakpoint(2, 'Call2')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_get_frame(thread_id, frame_id)

        writer_thread.write_remove_breakpoint(breakpoint_id)

        writer_thread.write_step_return(thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('109')
        thread_id = hit.thread_id
        frame_id = hit.frame_id
        line = hit.line

        assert line == 8, 'Expecting it to go to line 8. Went to: %s' % line

        writer_thread.write_step_in(thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('107')
        thread_id = hit.thread_id
        frame_id = hit.frame_id
        line = hit.line

        # goes to line 4 in jython (function declaration line)
        assert line in (4, 5), 'Expecting it to go to line 4 or 5. Went to: %s' % line

        writer_thread.write_run_thread(thread_id)

        assert 15 == writer_thread._sequence, 'Expected 15. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


def test_case_6(case_setup):
    with case_setup.test_file('_debugger_case56.py') as writer_thread:
        writer_thread.write_add_breakpoint(2, 'Call2')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_get_frame(thread_id, frame_id)

        writer_thread.write_step_return(thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('109')
        thread_id = hit.thread_id
        frame_id = hit.frame_id
        line = hit.line

        assert line == 8, 'Expecting it to go to line 8. Went to: %s' % line

        writer_thread.write_step_in(thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('107')
        thread_id = hit.thread_id
        frame_id = hit.frame_id
        line = hit.line

        # goes to line 4 in jython (function declaration line)
        assert line in (4, 5), 'Expecting it to go to line 4 or 5. Went to: %s' % line

        writer_thread.write_run_thread(thread_id)

        assert 13 == writer_thread._sequence, 'Expected 15. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_IRONPYTHON, reason='This test is flaky on Jython, so, skipping it.')
def test_case_7(case_setup):
    # This test checks that we start without variables and at each step a new var is created, but on ironpython,
    # the variables exist all at once (with None values), so, we can't test it properly.
    with case_setup.test_file('_debugger_case7.py') as writer_thread:
        writer_thread.write_add_breakpoint(2, 'Call')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111')

        writer_thread.write_get_frame(hit.thread_id, hit.frame_id)

        writer_thread.wait_for_vars('<xml></xml>')  # no vars at this point

        writer_thread.write_step_over(hit.thread_id)

        writer_thread.wait_for_breakpoint_hit('108')

        writer_thread.write_get_frame(hit.thread_id, hit.frame_id)

        writer_thread.wait_for_vars([
            [
                '<xml><var name="variable_for_test_1" type="int" qualifier="{0}" value="int%253A 10" />%0A</xml>'.format(builtin_qualifier),
                '<var name="variable_for_test_1" type="int"  value="int',  # jython
            ]
        ])

        writer_thread.write_step_over(hit.thread_id)

        writer_thread.wait_for_breakpoint_hit('108')

        writer_thread.write_get_frame(hit.thread_id, hit.frame_id)

        writer_thread.wait_for_vars([
            [
                '<xml><var name="variable_for_test_1" type="int" qualifier="{0}" value="int%253A 10" />%0A<var name="variable_for_test_2" type="int" qualifier="{0}" value="int%253A 20" />%0A</xml>'.format(builtin_qualifier),
                '<var name="variable_for_test_1" type="int"  value="int%253A 10" />%0A<var name="variable_for_test_2" type="int"  value="int%253A 20" />%0A',  # jython
            ]
        ])

        writer_thread.write_run_thread(hit.thread_id)

        assert 17 == writer_thread._sequence, 'Expected 17. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


def test_case_8(case_setup):
    with case_setup.test_file('_debugger_case89.py') as writer_thread:
        writer_thread.write_add_breakpoint(10, 'Method3')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111')

        writer_thread.write_step_return(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('109', line=15)

        writer_thread.write_run_thread(hit.thread_id)

        assert 9 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


def test_case_9(case_setup):
    with case_setup.test_file('_debugger_case89.py') as writer_thread:
        writer_thread.write_add_breakpoint(10, 'Method3')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111')

        # Note: no active exception (should not give an error and should return no
        # exception details as there's no exception).
        writer_thread.write_get_current_exception(hit.thread_id)

        msg = writer_thread.wait_for_message(accept_message=lambda msg:msg.strip().startswith(str(CMD_GET_EXCEPTION_DETAILS)))
        assert msg.thread['id'] == hit.thread_id
        assert not hasattr(msg.thread, 'frames')  # No frames should be found.

        writer_thread.write_step_over(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('108', line=11)

        writer_thread.write_step_over(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('108', line=12)

        writer_thread.write_run_thread(hit.thread_id)

        assert 13 == writer_thread._sequence, 'Expected 13. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


def test_case_10(case_setup):
    with case_setup.test_file('_debugger_case_simple_calls.py') as writer_thread:
        writer_thread.write_add_breakpoint(2, 'None')  # None or Method should make hit.
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111')

        writer_thread.write_step_return(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('109', line=11)

        writer_thread.write_step_over(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('108', line=12)

        writer_thread.write_run_thread(hit.thread_id)

        assert 11 == writer_thread._sequence, 'Expected 11. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


def test_case_11(case_setup):
    with case_setup.test_file('_debugger_case_simple_calls.py') as writer_thread:
        writer_thread.write_add_breakpoint(2, 'Method1')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111', line=2)

        writer_thread.write_step_over(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('108', line=3)

        writer_thread.write_step_over(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('108', line=11)

        writer_thread.write_step_over(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('108', line=12)

        writer_thread.write_run_thread(hit.thread_id)

        assert 13 == writer_thread._sequence, 'Expected 13. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


def test_case_12(case_setup):
    with case_setup.test_file('_debugger_case_simple_calls.py') as writer_thread:
        writer_thread.write_add_breakpoint(2, '')  # Should not be hit: setting empty function (not None) should only hit global.
        writer_thread.write_add_breakpoint(6, 'Method1a')
        writer_thread.write_add_breakpoint(11, 'Method2')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111', line=11)

        writer_thread.write_step_return(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('111', line=6)  # not a return (it stopped in the other breakpoint)

        writer_thread.write_run_thread(hit.thread_id)

        assert 13 == writer_thread._sequence, 'Expected 13. Had: %s' % writer_thread._sequence

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_IRONPYTHON, reason='Failing on IronPython (needs to be investigated).')
def test_case_13(case_setup):
    with case_setup.test_file('_debugger_case13.py') as writer_thread:

        def _ignore_stderr_line(line):
            if original_ignore_stderr_line(line):
                return True

            if IS_JYTHON:
                for expected in (
                    "RuntimeWarning: Parent module '_pydevd_bundle' not found while handling absolute import",
                    "import __builtin__"):
                    if expected in line:
                        return True

            return False

        original_ignore_stderr_line = writer_thread._ignore_stderr_line
        writer_thread._ignore_stderr_line = _ignore_stderr_line

        writer_thread.write_add_breakpoint(35, 'main')
        writer_thread.write("%s\t%s\t%s" % (CMD_SET_PROPERTY_TRACE, writer_thread.next_seq(), "true;false;false;true"))
        writer_thread.write_make_initial_run()
        hit = writer_thread.wait_for_breakpoint_hit('111')

        writer_thread.write_get_frame(hit.thread_id, hit.frame_id)

        writer_thread.write_step_in(hit.thread_id)
        hit = writer_thread.wait_for_breakpoint_hit('107', line=25)
        # Should go inside setter method

        writer_thread.write_step_in(hit.thread_id)
        hit = writer_thread.wait_for_breakpoint_hit('107')

        writer_thread.write_step_in(hit.thread_id)
        hit = writer_thread.wait_for_breakpoint_hit('107', line=21)
        # Should go inside getter method

        writer_thread.write_step_in(hit.thread_id)
        hit = writer_thread.wait_for_breakpoint_hit('107')

        # Disable property tracing
        writer_thread.write("%s\t%s\t%s" % (CMD_SET_PROPERTY_TRACE, writer_thread.next_seq(), "true;true;true;true"))
        writer_thread.write_step_in(hit.thread_id)
        hit = writer_thread.wait_for_breakpoint_hit('107', line=39)
        # Should Skip step into properties setter

        # Enable property tracing
        writer_thread.write("%s\t%s\t%s" % (CMD_SET_PROPERTY_TRACE, writer_thread.next_seq(), "true;false;false;true"))
        writer_thread.write_step_in(hit.thread_id)
        hit = writer_thread.wait_for_breakpoint_hit('107', line=8)
        # Should go inside getter method

        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


def test_case_14(case_setup):
    # Interactive Debug Console
    with case_setup.test_file('_debugger_case14.py') as writer_thread:
        writer_thread.write_add_breakpoint(22, 'main')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111')
        assert hit.thread_id, '%s not valid.' % hit.thread_id
        assert hit.frame_id, '%s not valid.' % hit.frame_id

        # Access some variable
        writer_thread.write_debug_console_expression("%s\t%s\tEVALUATE\tcarObj.color" % (hit.thread_id, hit.frame_id))
        writer_thread.wait_for_var(['<more>False</more>', '%27Black%27'])
        assert 7 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence

        # Change some variable
        writer_thread.write_debug_console_expression("%s\t%s\tEVALUATE\tcarObj.color='Red'" % (hit.thread_id, hit.frame_id))
        writer_thread.write_debug_console_expression("%s\t%s\tEVALUATE\tcarObj.color" % (hit.thread_id, hit.frame_id))
        writer_thread.wait_for_var(['<more>False</more>', '%27Red%27'])
        assert 11 == writer_thread._sequence, 'Expected 13. Had: %s' % writer_thread._sequence

        # Iterate some loop
        writer_thread.write_debug_console_expression("%s\t%s\tEVALUATE\tfor i in range(3):" % (hit.thread_id, hit.frame_id))
        writer_thread.wait_for_var(['<xml><more>True</more></xml>'])
        writer_thread.write_debug_console_expression("%s\t%s\tEVALUATE\t    print(i)" % (hit.thread_id, hit.frame_id))
        writer_thread.wait_for_var(['<xml><more>True</more></xml>'])
        writer_thread.write_debug_console_expression("%s\t%s\tEVALUATE\t" % (hit.thread_id, hit.frame_id))
        writer_thread.wait_for_var(
            [
                '<xml><more>False</more><output message="0"></output><output message="1"></output><output message="2"></output></xml>'            ]
            )
        assert 17 == writer_thread._sequence, 'Expected 19. Had: %s' % writer_thread._sequence

        writer_thread.write_run_thread(hit.thread_id)
        writer_thread.finished_ok = True


def test_case_15(case_setup):
    with case_setup.test_file('_debugger_case15.py') as writer_thread:
        writer_thread.write_add_breakpoint(22, 'main')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT)

        # Access some variable
        writer_thread.write_custom_operation("%s\t%s\tEXPRESSION\tcarObj.color" % (hit.thread_id, hit.frame_id), "EXEC", "f=lambda x: 'val=%s' % x", "f")
        writer_thread.wait_for_custom_operation('val=Black')
        assert 7 == writer_thread._sequence, 'Expected 7. Had: %s' % writer_thread._sequence

        writer_thread.write_custom_operation("%s\t%s\tEXPRESSION\tcarObj.color" % (hit.thread_id, hit.frame_id), "EXECFILE", debugger_unittest._get_debugger_test_file('_debugger_case15_execfile.py'), "f")
        writer_thread.wait_for_custom_operation('val=Black')
        assert 9 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence

        writer_thread.write_run_thread(hit.thread_id)
        writer_thread.finished_ok = True


def test_case_16(case_setup):
    # numpy.ndarray resolver
    try:
        import numpy
    except ImportError:
        pytest.skip('numpy not available')
    with case_setup.test_file('_debugger_case16.py') as writer_thread:
        writer_thread.write_add_breakpoint(9, 'main')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT)

        # In this test we check that the three arrays of different shapes, sizes and types
        # are all resolved properly as ndarrays.

        # First pass check is that we have all three expected variables defined
        writer_thread.write_get_frame(hit.thread_id, hit.frame_id)
        writer_thread.wait_for_multiple_vars((
            (
                '<var name="smallarray" type="ndarray" qualifier="numpy" value="ndarray%253A %255B 0.%252B1.j  1.%252B1.j  2.%252B1.j  3.%252B1.j  4.%252B1.j  5.%252B1.j  6.%252B1.j  7.%252B1.j  8.%252B1.j%250A  9.%252B1.j 10.%252B1.j 11.%252B1.j 12.%252B1.j 13.%252B1.j 14.%252B1.j 15.%252B1.j 16.%252B1.j 17.%252B1.j%250A 18.%252B1.j 19.%252B1.j 20.%252B1.j 21.%252B1.j 22.%252B1.j 23.%252B1.j 24.%252B1.j 25.%252B1.j 26.%252B1.j%250A 27.%252B1.j 28.%252B1.j 29.%252B1.j 30.%252B1.j 31.%252B1.j 32.%252B1.j 33.%252B1.j 34.%252B1.j 35.%252B1.j%250A 36.%252B1.j 37.%252B1.j 38.%252B1.j 39.%252B1.j 40.%252B1.j 41.%252B1.j 42.%252B1.j 43.%252B1.j 44.%252B1.j%250A 45.%252B1.j 46.%252B1.j 47.%252B1.j 48.%252B1.j 49.%252B1.j 50.%252B1.j 51.%252B1.j 52.%252B1.j 53.%252B1.j%250A 54.%252B1.j 55.%252B1.j 56.%252B1.j 57.%252B1.j 58.%252B1.j 59.%252B1.j 60.%252B1.j 61.%252B1.j 62.%252B1.j%250A 63.%252B1.j 64.%252B1.j 65.%252B1.j 66.%252B1.j 67.%252B1.j 68.%252B1.j 69.%252B1.j 70.%252B1.j 71.%252B1.j%250A 72.%252B1.j 73.%252B1.j 74.%252B1.j 75.%252B1.j 76.%252B1.j 77.%252B1.j 78.%252B1.j 79.%252B1.j 80.%252B1.j%250A 81.%252B1.j 82.%252B1.j 83.%252B1.j 84.%252B1.j 85.%252B1.j 86.%252B1.j 87.%252B1.j 88.%252B1.j 89.%252B1.j%250A 90.%252B1.j 91.%252B1.j 92.%252B1.j 93.%252B1.j 94.%252B1.j 95.%252B1.j 96.%252B1.j 97.%252B1.j 98.%252B1.j%250A 99.%252B1.j%255D" isContainer="True" />',
                '<var name="smallarray" type="ndarray" qualifier="numpy" value="ndarray%253A %255B  0.%252B1.j   1.%252B1.j   2.%252B1.j   3.%252B1.j   4.%252B1.j   5.%252B1.j   6.%252B1.j   7.%252B1.j%250A   8.%252B1.j   9.%252B1.j  10.%252B1.j  11.%252B1.j  12.%252B1.j  13.%252B1.j  14.%252B1.j  15.%252B1.j%250A  16.%252B1.j  17.%252B1.j  18.%252B1.j  19.%252B1.j  20.%252B1.j  21.%252B1.j  22.%252B1.j  23.%252B1.j%250A  24.%252B1.j  25.%252B1.j  26.%252B1.j  27.%252B1.j  28.%252B1.j  29.%252B1.j  30.%252B1.j  31.%252B1.j%250A  32.%252B1.j  33.%252B1.j  34.%252B1.j  35.%252B1.j  36.%252B1.j  37.%252B1.j  38.%252B1.j  39.%252B1.j%250A  40.%252B1.j  41.%252B1.j  42.%252B1.j  43.%252B1.j  44.%252B1.j  45.%252B1.j  46.%252B1.j  47.%252B1.j%250A  48.%252B1.j  49.%252B1.j  50.%252B1.j  51.%252B1.j  52.%252B1.j  53.%252B1.j  54.%252B1.j  55.%252B1.j%250A  56.%252B1.j  57.%252B1.j  58.%252B1.j  59.%252B1.j  60.%252B1.j  61.%252B1.j  62.%252B1.j  63.%252B1.j%250A  64.%252B1.j  65.%252B1.j  66.%252B1.j  67.%252B1.j  68.%252B1.j  69.%252B1.j  70.%252B1.j  71.%252B1.j%250A  72.%252B1.j  73.%252B1.j  74.%252B1.j  75.%252B1.j  76.%252B1.j  77.%252B1.j  78.%252B1.j  79.%252B1.j%250A  80.%252B1.j  81.%252B1.j  82.%252B1.j  83.%252B1.j  84.%252B1.j  85.%252B1.j  86.%252B1.j  87.%252B1.j%250A  88.%252B1.j  89.%252B1.j  90.%252B1.j  91.%252B1.j  92.%252B1.j  93.%252B1.j  94.%252B1.j  95.%252B1.j%250A  96.%252B1.j  97.%252B1.j  98.%252B1.j  99.%252B1.j%255D" isContainer="True" />'
            ),

            (
                '<var name="bigarray" type="ndarray" qualifier="numpy" value="ndarray%253A %255B%255B    0     1     2 ...  9997  9998  9999%255D%250A %255B10000 10001 10002 ... 19997 19998 19999%255D%250A %255B20000 20001 20002 ... 29997 29998 29999%255D%250A ...%250A %255B70000 70001 70002 ... 79997 79998 79999%255D%250A %255B80000 80001 80002 ... 89997 89998 89999%255D%250A %255B90000 90001 90002 ... 99997 99998 99999%255D%255D" isContainer="True" />',
                '<var name="bigarray" type="ndarray" qualifier="numpy" value="ndarray%253A %255B%255B    0     1     2 ...%252C  9997  9998  9999%255D%250A %255B10000 10001 10002 ...%252C 19997 19998 19999%255D%250A %255B20000 20001 20002 ...%252C 29997 29998 29999%255D%250A ...%252C %250A %255B70000 70001 70002 ...%252C 79997 79998 79999%255D%250A %255B80000 80001 80002 ...%252C 89997 89998 89999%255D%250A %255B90000 90001 90002 ...%252C 99997 99998 99999%255D%255D" isContainer="True" />'
            ),

            # Any of the ones below will do.
            (
                '<var name="hugearray" type="ndarray" qualifier="numpy" value="ndarray%253A %255B      0       1       2 ... 9999997 9999998 9999999%255D" isContainer="True" />',
                '<var name="hugearray" type="ndarray" qualifier="numpy" value="ndarray%253A %255B      0       1       2 ...%252C 9999997 9999998 9999999%255D" isContainer="True" />'
            )
        ))

        # For each variable, check each of the resolved (meta data) attributes...
        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'smallarray')
        writer_thread.wait_for_multiple_vars((
            '<var name="min" type="complex128"',
            '<var name="max" type="complex128"',
            '<var name="shape" type="tuple"',
            '<var name="dtype" type="dtype"',
            '<var name="size" type="int"',
        ))
        # ...and check that the internals are resolved properly
        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'smallarray\t__internals__')
        writer_thread.wait_for_var('<var name="%27size%27')

        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'bigarray')
        # isContainer could be true on some numpy versions, so, we only check for the var begin.
        writer_thread.wait_for_multiple_vars((
            [
                '<var name="min" type="int64" qualifier="numpy" value="int64%253A 0"',
                '<var name="min" type="int64" qualifier="numpy" value="int64%3A 0"',
                '<var name="size" type="int" qualifier="{0}" value="int%3A 100000"'.format(builtin_qualifier),
            ],
            [
                '<var name="max" type="int64" qualifier="numpy" value="int64%253A 99999"',
                '<var name="max" type="int32" qualifier="numpy" value="int32%253A 99999"',
                '<var name="max" type="int64" qualifier="numpy" value="int64%3A 99999"',
                '<var name="max" type="int32" qualifier="numpy" value="int32%253A 99999"',
            ],
            '<var name="shape" type="tuple"',
            '<var name="dtype" type="dtype"',
            '<var name="size" type="int"'
        ))
        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'bigarray\t__internals__')
        writer_thread.wait_for_var('<var name="%27size%27')

        # this one is different because it crosses the magic threshold where we don't calculate
        # the min/max
        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'hugearray')
        writer_thread.wait_for_var((
            [
                '<var name="min" type="str" qualifier={0} value="str%253A ndarray too big%252C calculating min would slow down debugging" />'.format(builtin_qualifier),
                '<var name="min" type="str" qualifier={0} value="str%3A ndarray too big%252C calculating min would slow down debugging" />'.format(builtin_qualifier),
                '<var name="min" type="str" qualifier="{0}" value="str%253A ndarray too big%252C calculating min would slow down debugging" />'.format(builtin_qualifier),
                '<var name="min" type="str" qualifier="{0}" value="str%3A ndarray too big%252C calculating min would slow down debugging" />'.format(builtin_qualifier),
            ],
            [
                '<var name="max" type="str" qualifier={0} value="str%253A ndarray too big%252C calculating max would slow down debugging" />'.format(builtin_qualifier),
                '<var name="max" type="str" qualifier={0} value="str%3A ndarray too big%252C calculating max would slow down debugging" />'.format(builtin_qualifier),
                '<var name="max" type="str" qualifier="{0}" value="str%253A ndarray too big%252C calculating max would slow down debugging" />'.format(builtin_qualifier),
                '<var name="max" type="str" qualifier="{0}" value="str%3A ndarray too big%252C calculating max would slow down debugging" />'.format(builtin_qualifier),
            ],
            '<var name="shape" type="tuple"',
            '<var name="dtype" type="dtype"',
            '<var name="size" type="int"',
        ))
        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'hugearray\t__internals__')
        writer_thread.wait_for_var('<var name="%27size%27')

        writer_thread.write_run_thread(hit.thread_id)
        writer_thread.finished_ok = True


def test_case_17(case_setup):
    # Check dont trace
    with case_setup.test_file('_debugger_case17.py') as writer_thread:
        writer_thread.write_enable_dont_trace(True)
        writer_thread.write_add_breakpoint(27, 'main')
        writer_thread.write_add_breakpoint(29, 'main')
        writer_thread.write_add_breakpoint(31, 'main')
        writer_thread.write_add_breakpoint(33, 'main')
        writer_thread.write_make_initial_run()

        for _i in range(4):
            hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT)

            writer_thread.write_step_in(hit.thread_id)
            hit = writer_thread.wait_for_breakpoint_hit('107', line=2)
            # Should Skip step into properties setter
            writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


def test_case_17a(case_setup):
    # Check dont trace return
    with case_setup.test_file('_debugger_case17a.py') as writer_thread:
        writer_thread.write_enable_dont_trace(True)
        writer_thread.write_add_breakpoint(2, 'm1')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT, line=2)

        writer_thread.write_step_in(hit.thread_id)
        hit = writer_thread.wait_for_breakpoint_hit('107', line=10)

        # Should Skip step into properties setter
        assert hit.name == 'm3'
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


def test_case_18(case_setup):
    # change local variable
    if IS_IRONPYTHON or IS_JYTHON:
        pytest.skip('Unsupported assign to local')

    with case_setup.test_file('_debugger_case18.py') as writer_thread:
        writer_thread.write_add_breakpoint(5, 'm2')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT, line=5)

        writer_thread.write_change_variable(hit.thread_id, hit.frame_id, 'a', '40')
        writer_thread.wait_for_var('<xml><var name="" type="int" qualifier="{0}" value="int%253A 40" />%0A</xml>'.format(builtin_qualifier,))
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


def test_case_19(case_setup):
    # Check evaluate '__' attributes
    with case_setup.test_file('_debugger_case19.py') as writer_thread:
        writer_thread.write_add_breakpoint(8, None)
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT, line=8)

        writer_thread.write_evaluate_expression('%s\t%s\t%s' % (hit.thread_id, hit.frame_id, 'LOCAL'), 'a.__var')
        writer_thread.wait_for_evaluation([
            [
                '<var name="a.__var" type="int" qualifier="{0}" value="int'.format(builtin_qualifier),
                '<var name="a.__var" type="int"  value="int',  # jython
            ]
        ])
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Monkey-patching related to starting threads not done on Jython.')
def test_case_20(case_setup):
    # Check that we were notified of threads creation before they started to run
    with case_setup.test_file('_debugger_case20.py') as writer_thread:
        writer_thread.write_make_initial_run()

        # We already check if it prints 'TEST SUCEEDED' by default, so, nothing
        # else should be needed in this test as it tests what's needed just by
        # running the module.
        writer_thread.finished_ok = True


@pytest.mark.skipif(not TEST_DJANGO, reason='No django available')
def test_case_django(case_setup_django):
    with case_setup_django.test_file('') as writer_thread:
        writer_thread.write_add_breakpoint_django(5, None, 'index.html')
        writer_thread.write_make_initial_run()

        t = writer_thread.create_request_thread('my_app')
        time.sleep(5)  # Give django some time to get to startup before requesting the page
        t.start()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT, line=5)
        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'entry')
        writer_thread.wait_for_vars([
            '<var name="key" type="str"',
            'v1'
        ])

        writer_thread.write_run_thread(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT, line=5)
        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'entry')
        writer_thread.wait_for_vars([
            '<var name="key" type="str"',
            'v2'
        ])

        writer_thread.write_run_thread(hit.thread_id)

        for _ in xrange(10):
            if hasattr(t, 'contents'):
                break
            time.sleep(.3)
        else:
            raise AssertionError('Django did not return contents properly!')

        contents = t.contents.replace(' ', '').replace('\r', '').replace('\n', '')
        if contents != '<ul><li>v1:v1</li><li>v2:v2</li></ul>':
            raise AssertionError('%s != <ul><li>v1:v1</li><li>v2:v2</li></ul>' % (contents,))

        writer_thread.finished_ok = True


@pytest.mark.skipif(not TEST_DJANGO, reason='No django available')
def test_case_django2(case_setup_django):
    with case_setup_django.test_file('') as writer_thread:
        writer_thread.write_add_breakpoint_django(4, None, 'name.html')
        writer_thread.write_make_initial_run()

        t = writer_thread.create_request_thread('my_app/name')
        time.sleep(5)  # Give django some time to get to startup before requesting the page
        t.start()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT, line=4)

        writer_thread.write_get_frame(hit.thread_id, hit.frame_id)
        writer_thread.wait_for_var('<var name="form" type="NameForm" qualifier="my_app.forms" value="NameForm%253A')
        writer_thread.write_run_thread(hit.thread_id)
        writer_thread.finished_ok = True


@pytest.mark.skipif(not TEST_CYTHON, reason='No cython available')
def test_cython(case_setup):
    from _pydevd_bundle import pydevd_cython
    assert pydevd_cython.trace_dispatch is not None


def _has_qt():
    try:
        from PySide import QtCore  # @UnresolvedImport
        return True
    except:
        try:
            from PyQt4 import QtCore
            return True
        except:
            try:
                from PyQt5 import QtCore
                return True
            except:
                pass
    return False


@pytest.mark.skipif(not _has_qt(), reason='No qt available')
def test_case_qthread1(case_setup):
    with case_setup.test_file('_debugger_case_qthread1.py') as writer_thread:
        breakpoint_id = writer_thread.write_add_breakpoint(19, 'run')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()

        writer_thread.write_remove_breakpoint(breakpoint_id)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.log.append('Checking sequence. Found: %s' % (writer_thread._sequence))
        assert 9 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(not _has_qt(), reason='No qt available')
def test_case_qthread2(case_setup):
    with case_setup.test_file('_debugger_case_qthread2.py') as writer_thread:
        breakpoint_id = writer_thread.write_add_breakpoint(24, 'long_running')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id

        writer_thread.write_remove_breakpoint(breakpoint_id)
        writer_thread.write_run_thread(thread_id)

        writer_thread.log.append('Checking sequence. Found: %s' % (writer_thread._sequence))
        assert 9 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(not _has_qt(), reason='No qt available')
def test_case_qthread3(case_setup):
    with case_setup.test_file('_debugger_case_qthread3.py') as writer_thread:
        breakpoint_id = writer_thread.write_add_breakpoint(22, 'run')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_remove_breakpoint(breakpoint_id)
        writer_thread.write_run_thread(thread_id)

        writer_thread.log.append('Checking sequence. Found: %s' % (writer_thread._sequence))
        assert 9 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(not _has_qt(), reason='No qt available')
def test_case_qthread4(case_setup):
    with case_setup.test_file('_debugger_case_qthread4.py') as writer_thread:
        original_additional_output_checks = writer_thread.additional_output_checks

        def additional_output_checks(stdout, stderr):
            original_additional_output_checks(stdout, stderr)
            if 'On start called' not in stdout:
                raise AssertionError('Expected "On start called" to be in stdout:\n%s' % (stdout,))
            if 'Done sleeping' not in stdout:
                raise AssertionError('Expected "Done sleeping" to be in stdout:\n%s' % (stdout,))
            if 'native Qt signal is not callable' in stderr:
                raise AssertionError('Did not expect "native Qt signal is not callable" to be in stderr:\n%s' % (stderr,))

        breakpoint_id = writer_thread.write_add_breakpoint(28, 'on_start')  # breakpoint on print('On start called2').
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()

        writer_thread.write_remove_breakpoint(breakpoint_id)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.log.append('Checking sequence. Found: %s' % (writer_thread._sequence))
        assert 9 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


def test_m_switch(case_setup_m_switch):
    with case_setup_m_switch.test_file() as writer_thread:
        writer_thread.log.append('writing add breakpoint')
        breakpoint_id = writer_thread.write_add_breakpoint(1, None)

        writer_thread.log.append('making initial run')
        writer_thread.write_make_initial_run()

        writer_thread.log.append('waiting for breakpoint hit')
        hit = writer_thread.wait_for_breakpoint_hit()

        writer_thread.write_remove_breakpoint(breakpoint_id)

        writer_thread.log.append('run thread')
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.log.append('asserting')
        try:
            assert 9 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence
        except:
            writer_thread.log.append('assert failed!')
            raise
        writer_thread.log.append('asserted')

        writer_thread.finished_ok = True


def test_module_entry_point(case_setup_m_switch_entry_point):
    with case_setup_m_switch_entry_point.test_file() as writer_thread:
        writer_thread.log.append('writing add breakpoint')
        breakpoint_id = writer_thread.write_add_breakpoint(1, None)

        writer_thread.log.append('making initial run')
        writer_thread.write_make_initial_run()

        writer_thread.log.append('waiting for breakpoint hit')
        hit = writer_thread.wait_for_breakpoint_hit()

        writer_thread.write_remove_breakpoint(breakpoint_id)

        writer_thread.log.append('run thread')
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.log.append('asserting')
        try:
            assert 9 == writer_thread._sequence, 'Expected 9. Had: %s' % writer_thread._sequence
        except:
            writer_thread.log.append('assert failed!')
            raise
        writer_thread.log.append('asserted')

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Failing on Jython -- needs to be investigated).')
def test_unhandled_exceptions_basic(case_setup):
    with case_setup.test_file('_debugger_case_unhandled_exceptions.py') as writer_thread:

        @overrides(writer_thread.check_test_suceeded_msg)
        def check_test_suceeded_msg(stdout, stderr):
            return 'TEST SUCEEDED' in ''.join(stdout) and 'TEST SUCEEDED' in ''.join(stderr)

        @overrides(writer_thread.additional_output_checks)
        def additional_output_checks(stdout, stderr):
            if 'raise Exception' not in stderr:
                raise AssertionError('Expected test to have an unhandled exception.\nstdout:\n%s\n\nstderr:\n%s' % (
                    stdout, stderr))

        # Don't call super (we have an unhandled exception in the stack trace).
        writer_thread.check_test_suceeded_msg = check_test_suceeded_msg
        writer_thread.additional_output_checks = additional_output_checks

        writer_thread.write_add_exception_breakpoint_with_policy('Exception', "0", "1", "0")
        writer_thread.write_make_initial_run()

        def check(hit, exc_type, exc_desc):
            writer_thread.write_get_current_exception(hit.thread_id)
            msg = writer_thread.wait_for_message(accept_message=lambda msg:exc_type in msg and 'exc_type="' in msg and 'exc_desc="' in msg, unquote_msg=False)
            assert unquote(msg.thread['exc_desc']) == exc_desc
            assert unquote(msg.thread['exc_type']) in (
                "&lt;type 'exceptions.%s'&gt;" % (exc_type,),  # py2
                "&lt;class '%s'&gt;" % (exc_type,)  # py3
            )
            if len(msg.thread.frame) == 0:
                assert unquote(unquote(msg.thread.frame['file'])).endswith('_debugger_case_unhandled_exceptions.py')
            else:
                assert unquote(unquote(msg.thread.frame[0]['file'])).endswith('_debugger_case_unhandled_exceptions.py')
            writer_thread.write_run_thread(hit.thread_id)

        # Will stop in 2 background threads
        hit0 = writer_thread.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
        thread_id1 = hit0.thread_id

        hit1 = writer_thread.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
        thread_id2 = hit1.thread_id

        if hit0.name == 'thread_func2':
            check(hit0, 'ValueError', 'in thread 2')
            check(hit1, 'Exception', 'in thread 1')
        else:
            check(hit0, 'Exception', 'in thread 1')
            check(hit1, 'ValueError', 'in thread 2')

        writer_thread.write_run_thread(thread_id1)
        writer_thread.write_run_thread(thread_id2)

        # Will stop in main thread
        hit = writer_thread.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
        assert hit.name == '<module>'
        thread_id3 = hit.thread_id

        # Requesting the stack in an unhandled exception should provide the stack of the exception,
        # not the current location of the program.
        writer_thread.write_get_thread_stack(thread_id3)
        msg = writer_thread.wait_for_message(lambda msg:msg.startswith('%s\t' % (CMD_GET_THREAD_STACK,)))
        assert len(msg.thread.frame) == 0  # In main thread (must have no back frames).
        assert msg.thread.frame['name'] == '<module>'
        check(hit, 'IndexError', 'in main')

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Failing on Jython -- needs to be investigated).')
def test_unhandled_exceptions_in_top_level(case_setup):
    # Note: expecting unhandled exception to be printed to stderr.
    with case_setup.test_file('_debugger_case_unhandled_exceptions_on_top_level.py') as writer_thread:

        @overrides(writer_thread.check_test_suceeded_msg)
        def check_test_suceeded_msg(stdout, stderr):
            return 'TEST SUCEEDED' in ''.join(stderr)

        @overrides(writer_thread.additional_output_checks)
        def additional_output_checks(stdout, stderr):
            # Don't call super as we have an expected exception
            if 'ValueError: TEST SUCEEDED' not in stderr:
                raise AssertionError('"ValueError: TEST SUCEEDED" not in stderr.\nstdout:\n%s\n\nstderr:\n%s' % (
                    stdout, stderr))

        writer_thread.additional_output_checks = additional_output_checks
        writer_thread.check_test_suceeded_msg = check_test_suceeded_msg

        writer_thread.write_add_exception_breakpoint_with_policy('Exception', "0", "1", "0")
        writer_thread.write_make_initial_run()

        # Will stop in main thread
        hit = writer_thread.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Failing on Jython -- needs to be investigated).')
def test_unhandled_exceptions_in_top_level2(case_setup):
    # Note: expecting unhandled exception to be printed to stderr.

    def get_environ(writer_thread):
        env = os.environ.copy()
        curr_pythonpath = env.get('PYTHONPATH', '')

        pydevd_dirname = os.path.dirname(writer_thread.get_pydevd_file())

        curr_pythonpath = pydevd_dirname + os.pathsep + curr_pythonpath
        env['PYTHONPATH'] = curr_pythonpath
        return env

    def check_test_suceeded_msg(writer_thread, stdout, stderr):
        return 'TEST SUCEEDED' in ''.join(stderr)

    def additional_output_checks(writer_thread, stdout, stderr):
        # Don't call super as we have an expected exception
        if 'ValueError: TEST SUCEEDED' not in stderr:
            raise AssertionError('"ValueError: TEST SUCEEDED" not in stderr.\nstdout:\n%s\n\nstderr:\n%s' % (
                stdout, stderr))

    def update_command_line_args(writer_thread, args):
        # Start pydevd with '-m' to see how it deal with being called with
        # runpy at the start.
        assert args[0].endswith('pydevd.py')
        args = ['-m', 'pydevd'] + args[1:]
        return args

    with case_setup.test_file(
            '_debugger_case_unhandled_exceptions_on_top_level.py',
            get_environ=get_environ,
            additional_output_checks=additional_output_checks,
            check_test_suceeded_msg=check_test_suceeded_msg,
            update_command_line_args=update_command_line_args,
            ) as writer_thread:

        writer_thread.write_add_exception_breakpoint_with_policy('Exception', "0", "1", "0")
        writer_thread.write_make_initial_run()

        # Should stop (only once) in the main thread.
        hit = writer_thread.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Failing on Jython -- needs to be investigated).')
def test_unhandled_exceptions_in_top_level3(case_setup):

    def check_test_suceeded_msg(writer_thread, stdout, stderr):
        return 'TEST SUCEEDED' in ''.join(stderr)

    def additional_output_checks(writer_thread, stdout, stderr):
        # Don't call super as we have an expected exception
        if 'ValueError: TEST SUCEEDED' not in stderr:
            raise AssertionError('"ValueError: TEST SUCEEDED" not in stderr.\nstdout:\n%s\n\nstderr:\n%s' % (
                stdout, stderr))

    with case_setup.test_file(
            '_debugger_case_unhandled_exceptions_on_top_level.py',
            additional_output_checks=additional_output_checks,
            check_test_suceeded_msg=check_test_suceeded_msg,
        ) as writer_thread:

        # Handled and unhandled
        writer_thread.write_add_exception_breakpoint_with_policy('Exception', "1", "1", "0")
        writer_thread.write_make_initial_run()

        # Will stop in main thread twice: once one we find that the exception is being
        # thrown and another in postmortem mode when we discover it's uncaught.
        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION)
        writer_thread.write_run_thread(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Failing on Jython -- needs to be investigated).')
def test_unhandled_exceptions_in_top_level4(case_setup):
    # Note: expecting unhandled exception to be printed to stderr.
    with case_setup.test_file('_debugger_case_unhandled_exceptions_on_top_level2.py') as writer_thread:

        @overrides(writer_thread.check_test_suceeded_msg)
        def check_test_suceeded_msg(self, stdout, stderr):
            return 'TEST SUCEEDED' in ''.join(stderr)

        @overrides(writer_thread.additional_output_checks)
        def additional_output_checks(self, stdout, stderr):
            # Don't call super as we have an expected exception
            assert 'ValueError: TEST SUCEEDED' in stderr

        writer_thread.additional_output_checks = additional_output_checks
        writer_thread.check_test_suceeded_msg = check_test_suceeded_msg

        # Handled and unhandled
        writer_thread.write_add_exception_breakpoint_with_policy('Exception', "1", "1", "0")
        writer_thread.write_make_initial_run()

        # We have an exception thrown and handled and another which is thrown and is then unhandled.
        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION)
        writer_thread.write_run_thread(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION)
        writer_thread.write_run_thread(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit(REASON_UNCAUGHT_EXCEPTION)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.log.append('Marking finished ok.')
        writer_thread.finished_ok = True


@pytest.mark.skipif(not IS_CPYTHON or (IS_PY36 and not IS_WINDOWS), reason='Only for Python (failing on 3.6 on travis (linux) -- needs to be investigated).')
def test_case_set_next_statement(case_setup):
    with case_setup.test_file('_debugger_case_set_next_statement.py') as writer_thread:
        breakpoint_id = writer_thread.write_add_breakpoint(6, None)
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT, line=6)

        writer_thread.write_evaluate_expression('%s\t%s\t%s' % (hit.thread_id, hit.frame_id, 'LOCAL'), 'a')
        writer_thread.wait_for_evaluation('<var name="a" type="int" qualifier="{0}" value="int: 2"'.format(builtin_qualifier))
        writer_thread.write_set_next_statement(hit.thread_id, 2, 'method')
        hit = writer_thread.wait_for_breakpoint_hit('127', line=2)

        writer_thread.write_step_over(hit.thread_id)
        hit = writer_thread.wait_for_breakpoint_hit('108')

        writer_thread.write_evaluate_expression('%s\t%s\t%s' % (hit.thread_id, hit.frame_id, 'LOCAL'), 'a')
        writer_thread.wait_for_evaluation('<var name="a" type="int" qualifier="{0}" value="int: 1"'.format(builtin_qualifier))

        writer_thread.write_remove_breakpoint(breakpoint_id)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


@pytest.mark.skipif(not IS_CPYTHON, reason='Only for Python.')
def test_case_get_next_statement_targets(case_setup):
    with case_setup.test_file('_debugger_case_get_next_statement_targets.py') as writer_thread:
        breakpoint_id = writer_thread.write_add_breakpoint(21, None)
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_STOP_ON_BREAKPOINT, line=21)

        writer_thread.write_get_next_statement_targets(hit.thread_id, hit.frame_id)
        targets = writer_thread.wait_for_get_next_statement_targets()
        expected = set((2, 3, 5, 8, 9, 10, 12, 13, 14, 15, 17, 18, 19, 21))
        assert targets == expected, 'Expected targets to be %s, was: %s' % (expected, targets)

        writer_thread.write_remove_breakpoint(breakpoint_id)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_IRONPYTHON or IS_JYTHON, reason='Failing on IronPython and Jython (needs to be investigated).')
def test_case_type_ext(case_setup):
    # Custom type presentation extensions

    def get_environ(self):
        env = os.environ.copy()

        python_path = env.get("PYTHONPATH", "")
        ext_base = debugger_unittest._get_debugger_test_file('my_extensions')
        env['PYTHONPATH'] = ext_base + os.pathsep + python_path  if python_path else ext_base
        return env

    with case_setup.test_file('_debugger_case_type_ext.py', get_environ=get_environ) as writer_thread:
        writer_thread.get_environ = get_environ

        writer_thread.write_add_breakpoint(7, None)
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111')
        writer_thread.write_get_frame(hit.thread_id, hit.frame_id)
        assert writer_thread.wait_for_var([
            [
                r'<var name="my_rect" type="Rect" qualifier="__main__" value="Rectangle%255BLength%253A 5%252C Width%253A 10 %252C Area%253A 50%255D" isContainer="True" />',
                r'<var name="my_rect" type="Rect"  value="Rect: <__main__.Rect object at',  # Jython
            ]
        ])
        writer_thread.write_get_variable(hit.thread_id, hit.frame_id, 'my_rect')
        assert writer_thread.wait_for_var(r'<var name="area" type="int" qualifier="{0}" value="int%253A 50" />'.format(builtin_qualifier))
        writer_thread.write_run_thread(hit.thread_id)
        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_IRONPYTHON or IS_JYTHON, reason='Failing on IronPython and Jython (needs to be investigated).')
def test_case_event_ext(case_setup):

    def get_environ(self):
        env = os.environ.copy()

        python_path = env.get("PYTHONPATH", "")
        ext_base = debugger_unittest._get_debugger_test_file('my_extensions')
        env['PYTHONPATH'] = ext_base + os.pathsep + python_path  if python_path else ext_base
        env["VERIFY_EVENT_TEST"] = "1"
        return env

    # Test initialize event for extensions
    with case_setup.test_file('_debugger_case_event_ext.py', get_environ=get_environ) as writer_thread:

        original_additional_output_checks = writer_thread.additional_output_checks

        @overrides(writer_thread.additional_output_checks)
        def additional_output_checks(stdout, stderr):
            original_additional_output_checks(stdout, stderr)
            if 'INITIALIZE EVENT RECEIVED' not in stdout:
                raise AssertionError('No initialize event received')

        writer_thread.additional_output_checks = additional_output_checks

        writer_thread.write_make_initial_run()
        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Jython does not seem to be creating thread started inside tracing (investigate).')
def test_case_writer_thread_creation_deadlock(case_setup):
    # check case where there was a deadlock evaluating expressions
    with case_setup.test_file('_debugger_case_thread_creation_deadlock.py') as writer_thread:
        writer_thread.write_add_breakpoint(26, None)
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111')

        assert hit.line == 26, 'Expected return to be in line 26, was: %s' % (hit.line,)

        writer_thread.write_evaluate_expression('%s\t%s\t%s' % (hit.thread_id, hit.frame_id, 'LOCAL'), 'create_thread()')
        writer_thread.wait_for_evaluation('<var name="create_thread()" type="str" qualifier="{0}" value="str: create_thread:ok'.format(builtin_qualifier))
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


def test_case_skip_breakpoints_in_exceptions(case_setup):
    # Case where breakpoint is skipped after an exception is raised over it
    with case_setup.test_file('_debugger_case_skip_breakpoint_in_exceptions.py') as writer_thread:
        writer_thread.write_add_breakpoint(5, None)
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit('111', line=5)
        writer_thread.write_run_thread(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit('111', line=5)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


def test_case_handled_exceptions0(case_setup):
    # Stop only once per handled exception.
    with case_setup.test_file('_debugger_case_exceptions.py') as writer_thread:
        writer_thread.write_set_project_roots([os.path.dirname(writer_thread.TEST_FILE)])
        writer_thread.write_add_exception_breakpoint_with_policy(
            'IndexError',
            notify_on_handled_exceptions=2,  # Notify only once
            notify_on_unhandled_exceptions=0,
            ignore_libraries=1
        )
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION, line=3)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Not working on Jython (needs to be investigated).')
def test_case_handled_exceptions1(case_setup):

    # Stop multiple times for the same handled exception.
    def get_environ(self):
        env = os.environ.copy()

        env["IDE_PROJECT_ROOTS"] = os.path.dirname(self.TEST_FILE)
        return env

    with case_setup.test_file('_debugger_case_exceptions.py', get_environ=get_environ) as writer_thread:
        writer_thread.write_add_exception_breakpoint_with_policy(
            'IndexError',
            notify_on_handled_exceptions=1,  # Notify multiple times
            notify_on_unhandled_exceptions=0,
            ignore_libraries=1
        )
        writer_thread.write_make_initial_run()

        def check(hit):
            writer_thread.write_get_frame(hit.thread_id, hit.frame_id)
            writer_thread.wait_for_message(accept_message=lambda msg:'__exception__' in msg and 'IndexError' in msg, unquote_msg=False)
            writer_thread.write_get_current_exception(hit.thread_id)
            msg = writer_thread.wait_for_message(accept_message=lambda msg:'IndexError' in msg and 'exc_type="' in msg and 'exc_desc="' in msg, unquote_msg=False)
            assert msg.thread['exc_desc'] == 'foo'
            assert unquote(msg.thread['exc_type']) in (
                "&lt;type 'exceptions.IndexError'&gt;",  # py2
                "&lt;class 'IndexError'&gt;"  # py3
            )

            assert unquote(unquote(msg.thread.frame[0]['file'])).endswith('_debugger_case_exceptions.py')
            writer_thread.write_run_thread(hit.thread_id)

        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION, line=3)
        check(hit)

        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION, line=6)
        check(hit)

        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION, line=10)
        check(hit)

        writer_thread.finished_ok = True


def test_case_handled_exceptions2(case_setup):

    # No IDE_PROJECT_ROOTS set.
    def get_environ(self):
        env = os.environ.copy()

        # Don't stop anywhere (note: having IDE_PROJECT_ROOTS = '' will consider
        # having anything not under site-packages as being in the project).
        env["IDE_PROJECT_ROOTS"] = '["empty"]'
        return env

    with case_setup.test_file('_debugger_case_exceptions.py', get_environ=get_environ) as writer_thread:
        writer_thread.write_add_exception_breakpoint_with_policy(
            'IndexError',
            notify_on_handled_exceptions=1,  # Notify multiple times
            notify_on_unhandled_exceptions=0,
            ignore_libraries=1
        )
        writer_thread.write_make_initial_run()

        writer_thread.finished_ok = True


def test_case_handled_exceptions3(case_setup):

    # Don't stop on exception thrown in the same context (only at caller).
    def get_environ(self):
        env = os.environ.copy()

        env["IDE_PROJECT_ROOTS"] = os.path.dirname(self.TEST_FILE)
        return env

    with case_setup.test_file('_debugger_case_exceptions.py', get_environ=get_environ) as writer_thread:
        # Note: in this mode we'll only stop once.
        writer_thread.write_set_py_exception_globals(
            break_on_uncaught=False,
            break_on_caught=True,
            skip_on_exceptions_thrown_in_same_context=False,
            ignore_exceptions_thrown_in_lines_with_ignore_exception=True,
            ignore_libraries=True,
            exceptions=('IndexError',)
        )

        writer_thread.write_make_initial_run()
        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION, line=3)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


def test_case_handled_exceptions4(case_setup):

    # Don't stop on exception thrown in the same context (only at caller).
    def get_environ(self):
        env = os.environ.copy()

        env["IDE_PROJECT_ROOTS"] = os.path.dirname(self.TEST_FILE)
        return env

    with case_setup.test_file('_debugger_case_exceptions.py', get_environ=get_environ) as writer_thread:
        # Note: in this mode we'll only stop once.
        writer_thread.write_set_py_exception_globals(
            break_on_uncaught=False,
            break_on_caught=True,
            skip_on_exceptions_thrown_in_same_context=True,
            ignore_exceptions_thrown_in_lines_with_ignore_exception=True,
            ignore_libraries=True,
            exceptions=('IndexError',)
        )

        writer_thread.write_make_initial_run()
        hit = writer_thread.wait_for_breakpoint_hit(REASON_CAUGHT_EXCEPTION, line=6)
        writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


def test_case_settrace(case_setup):
    with case_setup.test_file('_debugger_case_settrace.py') as writer_thread:
        self.write_make_initial_run()

        hit = self.wait_for_breakpoint_hit('108', line=12)
        self.write_run_thread(hit.thread_id)

        hit = self.wait_for_breakpoint_hit(REASON_THREAD_SUSPEND, line=7)
        self.write_run_thread(hit.thread_id)

        self.finished_ok = True


@pytest.mark.skipif(IS_PY26 or IS_JYTHON, reason='scapy only supports 2.7 onwards, not available for jython.')
def test_case_scapy(case_setup):
    with case_setup.test_file('_debugger_case_scapy.py') as writer_thread:
        writer_thread.reader_thread.set_timeout(30)  # Starting scapy may be slow (timed out with 15 seconds on appveyor).
        writer_thread.write_add_breakpoint(2, None)
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_run_thread(thread_id)
        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_APPVEYOR or IS_JYTHON, reason='Flaky on appveyor / Jython encoding issues (needs investigation).')
def test_redirect_output(case_setup):

    def get_environ(self):
        env = os.environ.copy()

        env["PYTHONIOENCODING"] = 'utf-8'
        return env

    with case_setup.test_file('_debugger_case_redirect.py') as writer_thread:
        original_ignore_stderr_line = writer_thread._ignore_stderr_line

        @overrides(writer_thread._ignore_stderr_line)
        def _ignore_stderr_line(line):
            if original_ignore_stderr_line(line):
                return True
            return line.startswith((
                'text',
                'binary',
                'a'
            ))

        writer_thread._ignore_stderr_line = _ignore_stderr_line

        # Note: writes to stdout and stderr are now synchronous (so, the order
        # must always be consistent and there's a message for each write).
        expected = [
            'text\n',
            'binary or text\n',
            'ação1\n',
        ]

        if sys.version_info[0] >= 3:
            expected.extend((
                'binary\n',
                'ação2\n'.encode(encoding='latin1').decode('utf-8', 'replace'),
                'ação3\n',
            ))

        new_expected = [(x, 'stdout') for x in expected]
        new_expected.extend([(x, 'stderr') for x in expected])

        writer_thread.write_start_redirect()

        writer_thread.write_make_initial_run()
        msgs = []
        while len(msgs) < len(new_expected):
            msg = writer_thread.wait_for_output()
            if msg not in new_expected:
                continue
            msgs.append(msg)

        if msgs != new_expected:
            print(msgs)
            print(new_expected)
        assert msgs == new_expected
        writer_thread.finished_ok = True


def test_path_translation(case_setup):

    def get_file_in_client(writer_thread):
        # Instead of using: test_python/_debugger_case_path_translation.py
        # we'll set the breakpoints at foo/_debugger_case_path_translation.py
        file_in_client = os.path.dirname(os.path.dirname(writer_thread.TEST_FILE))
        return os.path.join(os.path.dirname(file_in_client), 'foo', '_debugger_case_path_translation.py')

    def get_environ(writer_thread):
        import json
        env = os.environ.copy()

        env["PYTHONIOENCODING"] = 'utf-8'

        assert writer_thread.TEST_FILE.endswith('_debugger_case_path_translation.py')
        env["PATHS_FROM_ECLIPSE_TO_PYTHON"] = json.dumps([
            (
                os.path.dirname(get_file_in_client(writer_thread)),
                os.path.dirname(writer_thread.TEST_FILE)
            )
        ])
        return env

    with case_setup.test_file('_debugger_case_path_translation.py', get_environ=get_environ) as writer_thread:
        from tests_python.debugger_unittest import CMD_LOAD_SOURCE
        writer_thread.write_start_redirect()

        file_in_client = get_file_in_client(writer_thread)
        assert 'tests_python' not in file_in_client
        writer_thread.write_add_breakpoint(2, 'main', filename=file_in_client)
        writer_thread.write_make_initial_run()

        xml = writer_thread.wait_for_message(lambda msg:'stop_reason="111"' in msg)
        assert xml.thread.frame[0]['file'] == file_in_client
        thread_id = xml.thread['id']

        # Request a file that exists
        files_to_match = [file_in_client]
        if IS_WINDOWS:
            files_to_match.append(file_in_client.upper())
        for f in files_to_match:
            writer_thread.write_load_source(f)
            writer_thread.wait_for_message(
                lambda msg:
                    '%s\t' % CMD_LOAD_SOURCE in msg and \
                    "def main():" in msg and \
                    "print('break here')" in msg and \
                    "print('TEST SUCEEDED!')" in msg
                , expect_xml=False)

        # Request a file that does not exist
        writer_thread.write_load_source(file_in_client + 'not_existent.py')
        writer_thread.wait_for_message(
            lambda msg:'901\t' in msg and ('FileNotFoundError' in msg or 'IOError' in msg),
            expect_xml=False)

        writer_thread.write_run_thread(thread_id)

        writer_thread.finished_ok = True


def test_evaluate_errors(case_setup):
    with case_setup.test_file('_debugger_case7.py') as writer_thread:
        writer_thread.write_add_breakpoint(4, 'Call')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_evaluate_expression('%s\t%s\t%s' % (thread_id, frame_id, 'LOCAL'), 'name_error')
        writer_thread.wait_for_evaluation('<var name="name_error" type="NameError"')
        writer_thread.write_run_thread(thread_id)
        writer_thread.finished_ok = True


def test_list_threads(case_setup):
    with case_setup.test_file('_debugger_case7.py') as writer_thread:
        writer_thread.write_add_breakpoint(4, 'Call')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        seq = writer_thread.write_list_threads()
        msg = writer_thread.wait_for_list_threads(seq)
        assert msg.thread['name'] == 'MainThread'
        assert msg.thread['id'].startswith('pid')
        writer_thread.write_run_thread(thread_id)
        writer_thread.finished_ok = True


def test_case_print(case_setup):
    with case_setup.test_file('_debugger_case_print.py') as writer_thread:
        writer_thread.write_add_breakpoint(1, 'None')
        writer_thread.write_make_initial_run()

        hit = writer_thread.wait_for_breakpoint_hit()
        thread_id = hit.thread_id
        frame_id = hit.frame_id

        writer_thread.write_run_thread(thread_id)

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Not working on Jython (needs to be investigated).')
def test_case_lamdda(case_setup):
    with case_setup.test_file('_debugger_case_lamda.py') as writer_thread:
        writer_thread.write_add_breakpoint(1, 'None')
        writer_thread.write_make_initial_run()

        for _ in range(3):  # We'll hit the same breakpoint 3 times.
            hit = writer_thread.wait_for_breakpoint_hit()

            writer_thread.write_run_thread(hit.thread_id)

        writer_thread.finished_ok = True


@pytest.mark.skipif(IS_JYTHON, reason='Not working properly on Jython (needs investigation).')
def test_case_suspension_policy(case_setup):
    case_setup.check_case(WriterCaseBreakpointSuspensionPolicy)


def test_case_get_thread_stack(case_setup):
    case_setup.check_case(WriterCaseGetThreadStack)


def test_case_dump_threads_to_stderr(case_setup):
    case_setup.check_case(WriterCaseDumpThreadsToStderr)


def test_stop_on_start_regular(case_setup):
    case_setup.check_case(WriterCaseStopOnStartRegular)


def test_stop_on_start_m_switch(case_setup):
    case_setup.check_case(WriterCaseStopOnStartMSwitch)


def test_stop_on_start_entry_point(case_setup):
    case_setup.check_case(WriterCaseStopOnStartEntryPoint)


#=======================================================================================================================
# Test
#=======================================================================================================================
class Test(unittest.TestCase, debugger_unittest.DebuggerRunner):

    @pytest.mark.skipif(IS_JYTHON, reason='Not working properly on Jython (needs investigation).')
    def test_debug_zip_files(case_setup):
        case_setup.check_case(WriterDebugZipFiles(case_setup.tmpdir))


@pytest.mark.skipif(not IS_CPYTHON, reason='CPython only test.')
class TestPythonRemoteDebugger(unittest.TestCase, debugger_unittest.DebuggerRunner):

    def get_command_line(self):
        return [sys.executable, '-u']

    def add_command_line_args(self, args):
        writer_thread = self.writer_thread

        ret = args + [self.writer_thread.TEST_FILE]
        ret = writer_thread.update_command_line_args(ret)  # Provide a hook for the writer
        return ret

    def test_remote_debugger(self):
        self.check_case(WriterThreadCaseRemoteDebugger)

    def test_remote_debugger2(self):
        self.check_case(WriterThreadCaseRemoteDebuggerMultiProc)

    def test_remote_unhandled_exceptions(self):
        self.check_case(WriterThreadCaseRemoteDebuggerUnhandledExceptions)

    def test_remote_unhandled_exceptions2(self):
        self.check_case(WriterThreadCaseRemoteDebuggerUnhandledExceptions2)


def get_java_location():
    from java.lang import System  # @UnresolvedImport
    jre_dir = System.getProperty("java.home")
    for f in [os.path.join(jre_dir, 'bin', 'java.exe'), os.path.join(jre_dir, 'bin', 'java')]:
        if os.path.exists(f):
            return f
    raise RuntimeError('Unable to find java executable')


def get_jython_jar():
    from java.lang import ClassLoader  # @UnresolvedImport
    cl = ClassLoader.getSystemClassLoader()
    paths = map(lambda url: url.getFile(), cl.getURLs())
    for p in paths:
        if 'jython.jar' in p:
            return p
    raise RuntimeError('Unable to find jython.jar')


def get_location_from_line(line):
    loc = line.split('=')[1].strip()
    if loc.endswith(';'):
        loc = loc[:-1]
    if loc.endswith('"'):
        loc = loc[:-1]
    if loc.startswith('"'):
        loc = loc[1:]
    return loc


def split_line(line):
    if '=' not in line:
        return None, None
    var = line.split('=')[0].strip()
    return var, get_location_from_line(line)

# c:\bin\jython2.7.0\bin\jython.exe -m py.test tests_python
