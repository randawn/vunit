# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2016-2017, Lars Asplund lars.anders.asplund@gmail.com

"""
Shared simulation logic between vsim based simulators such as ModelSim
and RivieraPRO
"""

import sys
import os
from os.path import join, dirname, abspath
from vunit.ostools import (write_file,
                           Process)
from vunit.persistent_tcl_shell import PersistentTclShell


class VsimSimulatorMixin(object):
    """
    A Mixin class for parts that are common to vsim/TCL based
    simulators such as modelsim and rivierapro
    """

    def __init__(self, prefix, persistent, gui, sim_cfg_file_name):
        self._prefix = prefix
        sim_cfg_file_name = abspath(sim_cfg_file_name)
        self._gui = gui
        self._sim_cfg_file_name = sim_cfg_file_name

        prefix = self._prefix  # Avoid circular dependency inhibiting process destruction
        env = self.get_env()

        def create_process(ident):
            return Process([join(prefix, "vsim"), "-c",
                            "-l", join(dirname(sim_cfg_file_name), "transcript%i" % ident),
                            "-do", abspath(join(dirname(__file__), "tcl_read_eval_loop.tcl"))],
                           cwd=dirname(sim_cfg_file_name),
                           env=env)

        if persistent:
            self._persistent_shell = PersistentTclShell(create_process=create_process)
        else:
            self._persistent_shell = None

    @staticmethod
    def _create_restart_function():
        """"
        Create the vunit_restart function to recompile and restart the simulation

        This function is quite complicated to work around limitations
        of modelsim not being able to change working directory.

        Thus python is called with an explicit command string that in
        turn call the python command we actually wanted but in the
        correct working directory using subprocess.call

        -u flag is needed for continuous output
        """
        recompile_command = [
            sys.executable,
            "-u",
            sys.argv[0],
            "--compile"] + sys.argv[1:]
        recompile_command_visual = " ".join(recompile_command)

        # stderr is intentionally re-directed to stdout so that the tcl's catch
        # relies on the return code from the python process rather than being
        # tricked by output going to stderr.  See issue #228.
        recompile_command_eval = [
            "%s" % sys.executable,
            "-u",
            "-c",
            ("import sys;"
             "import subprocess;"
             "exit(subprocess.call(%r, "
             "cwd=%r, "
             "bufsize=0, "
             "universal_newlines=True, "
             "stdout=sys.stdout, "
             "stderr=sys.stdout))") % (recompile_command, abspath(os.getcwd()))]
        recompile_command_eval_tcl = " ".join(["{%s}" % part for part in recompile_command_eval])

        tcl = """
proc vunit_compile {} {
    set cmd_show {%s}
    puts "Re-compiling using command ${cmd_show}"

    set chan [open |[list %s] r]

    while {[gets $chan line] >= 0} {
        puts $line
    }

    if {[catch {close $chan} error_msg]} {
        puts "Re-compile failed"
        puts ${error_msg}
        return 1
    } else {
        puts "Re-compile finished"
        return 0
    }
}

proc vunit_restart {} {
    if {![vunit_compile]} {
        _vunit_sim_restart
        vunit_run
    }
}
""" % (recompile_command_visual, recompile_command_eval_tcl)
        return tcl

    def _create_common_script(self,
                              test_suite_name,
                              config,
                              output_path):
        """
        Create tcl script with functions common to interactive and batch modes
        """
        tcl = """
proc vunit_help {} {
    puts {List of VUnit commands:}
    puts {vunit_help}
    puts {  - Prints this help}
    puts {vunit_load [vsim_extra_args]}
    puts {  - Load design with correct generics for the test}
    puts {  - Optional first argument are passed as extra flags to vsim}
    puts {vunit_user_init}
    puts {  - Re-runs the user defined init file}
    puts {vunit_run}
    puts {  - Run test, must do vunit_load first}
    puts {vunit_compile}
    puts {  - Recompiles the source files}
    puts {vunit_restart}
    puts {  - Recompiles the source files}
    puts {  - and re-runs the simulation if the compile was successful}
}
"""
        tcl += self._create_load_function(test_suite_name, config, output_path)
        tcl += self._create_run_function()
        tcl += self._create_restart_function()
        return tcl

    @staticmethod
    def _create_batch_script(common_file_name, load_only=False):
        """
        Create tcl script to run in batch mode
        """
        batch_do = ""
        batch_do += "onerror {quit -code 1}\n"
        batch_do += "source \"%s\"\n" % fix_path(common_file_name)
        batch_do += "set failed [vunit_load]\n"
        batch_do += "if {$failed} {quit -code 1}\n"
        if not load_only:
            batch_do += "set failed [vunit_run]\n"
            batch_do += "if {$failed} {quit -code 1}\n"
        batch_do += "quit -code 0\n"
        return batch_do

    def _create_user_init_function(self, config):
        """
        Create the vunit_user_init function which sources the user defined TCL file in
        simulator_name.init_file.gui
        """
        init_file = config.sim_options.get(self.name + ".init_file.gui", None)
        tcl = "proc vunit_user_init {} {\n"
        if init_file is not None:
            tcl += '  source "%s"\n' % fix_path(abspath(init_file))
        tcl += "}\n"
        return tcl

    def _create_gui_script(self, common_file_name, config):
        """
        Create the user facing script which loads common functions and prints a help message
        """
        tcl = 'source "%s"\n' % fix_path(common_file_name)
        tcl += self._create_user_init_function(config)
        tcl += "if {![vunit_load]} {\n"
        tcl += "  vunit_user_init\n"
        tcl += "  vunit_help\n"
        tcl += "}\n"
        return tcl

    def _run_batch_file(self, batch_file_name, gui=False):
        """
        Run a test bench in batch by invoking a new vsim process from the command line
        """

        try:
            args = [join(self._prefix, "vsim"), "-gui" if gui else "-c",
                    "-l", join(dirname(batch_file_name), "transcript"),
                    '-do', "source \"%s\"" % fix_path(batch_file_name)]

            proc = Process(args, cwd=dirname(self._sim_cfg_file_name))
            proc.consume_output()
        except Process.NonZeroExitCode:
            return False
        return True

    def _run_persistent(self, common_file_name, load_only=False):
        """
        Run a test bench using the persistent vsim process
        """
        try:
            self._persistent_shell.execute('source "%s"' % fix_path(common_file_name))
            self._persistent_shell.execute("set failed [vunit_load]")
            if self._persistent_shell.read_var("failed") == '1':
                return False

            run_ok = True
            if not load_only:
                self._persistent_shell.execute("set failed [vunit_run]")
                run_ok = self._persistent_shell.read_var("failed") != '1'
            self._persistent_shell.execute("quit -sim")
            return run_ok
        except Process.NonZeroExitCode:
            return False

    def simulate(self, output_path, test_suite_name, config, elaborate_only):
        """
        Run a test bench
        """
        sim_output_path = abspath(join(output_path, self.name))
        common_file_name = join(sim_output_path, "common.do")
        gui_file_name = join(sim_output_path, "gui.do")
        batch_file_name = join(sim_output_path, "batch.do")

        write_file(common_file_name,
                   self._create_common_script(test_suite_name,
                                              config,
                                              sim_output_path))
        write_file(gui_file_name,
                   self._create_gui_script(common_file_name, config))
        write_file(batch_file_name,
                   self._create_batch_script(common_file_name, elaborate_only))

        if self._gui:
            return self._run_batch_file(gui_file_name, gui=True)
        elif self._persistent_shell is not None:
            return self._run_persistent(common_file_name, load_only=elaborate_only)
        else:
            return self._run_batch_file(batch_file_name)


def fix_path(path):
    """
    Adjust path for TCL usage
    """
    return path.replace("\\", "/").replace(" ", "\\ ")
