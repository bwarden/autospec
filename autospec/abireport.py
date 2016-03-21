#!/bin/true
#
# abireport.py - part of autospec
# Copyright (C) 2016 Intel Corporation
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Generate a symbols file from all shared libraries encountered, enabling
# a consistent ABI report for every build. We ensure that everything is
# appropriately sorted, in that a diff only occurs when the shared libraries
# in the package themselves actually change too.

import subprocess
import re
import os
import sys
import util
import shutil

valid_file = ".* ELF (64|32)\-bit LSB shared object,"

valid_dirs = ["/usr/lib", "/usr/lib64"]

reg = re.compile(valid_file)

wanted_symbol_types = ["A", "T"]

ignored_symbols = [
    "__bss_start",
    "_edata",
    "_end",
    "_fini",
    "_init",
]


def get_output(cmd):
    try:
        o = subprocess.getoutput(cmd)
        return o
    except Exception as e:
        print("Error: %s" % e)


def get_soname(path):
    cmd = "objdump -p \"{}\"|grep SONAME".format(path)
    try:
        line = get_output(cmd)
        if "SONAME" not in line:
            return None
        line = line.strip()
        spl = line.split()[1]
        return spl
    except Exception:
        return None


def is_file_valid(path):
    if not os.path.exists(path) or os.path.islink(path):
        return False
    cmd = "file \"{}\"".format(path)
    try:
        line = get_output(cmd).split("\n")[0]
        if reg.match(line):
            return True
    except Exception as e:
        print(e)
        return False


def dump_symbols(path):
    cmd = "nm --defined-only -g --dynamic \"{}\"".format(path)
    lines = None

    ret = set()

    try:
        lines = get_output(cmd)
    except Exception as e:
        print("Fatal error inspecting {}: {}".format(path, e))
        sys.exit(1)
    for line in lines.split("\n"):
        line = line.strip()

        spl = line.split()
        if len(spl) != 3:
            continue
        sym_type = spl[1]
        sym_id = spl[2]

        if sym_type not in wanted_symbol_types:
            continue
        if sym_id in ignored_symbols:
            continue
        ret.add(sym_id)
    return ret


def purge_tree(tree):
    if not os.path.exists(tree):
        return
    try:
        shutil.rmtree(tree)
    except Exception as e:
        util.print_fatal("Cannot remove tree: {}".format(e))
        sys.exit(1)


def truncate_file(path):
    if not os.path.exists(path):
        return
    with open(path, "rw+") as trunc:
        trunc.truncate()


def examine_abi(download_path):
    results_dir = os.path.abspath(os.path.join(download_path, "results"))
    download_path = os.path.abspath(download_path)

    if not os.path.exists(results_dir):
        util.print_fatal("Results directory does not exist, aborting")
        sys.exit(1)

    old_dir = os.getcwd()

    rpms = set()
    for item in os.listdir(results_dir):
        if item.endswith(".rpm") and not item.endswith(".src.rpm"):
            rpms.add(os.path.basename(item))

    if len(rpms) == 0:
        util.print_fatal("No usable rpms found, aborting")
        sys.exit(1)

    extract_dir = os.path.abspath(os.path.join(download_path, "__extraction"))
    purge_tree(extract_dir)

    try:
        os.makedirs(extract_dir)
    except Exception as e:
        util.print_fatal("Cannot create extraction tree: {}".format(e))
        sys.exit(1)

    os.chdir(extract_dir)

    # Extract all those rpms to our current directory
    try:
        for rpm in rpms:
            cmd = "rpm2cpio \"{}\" | cpio -imd 2>/dev/null".format(os.path.join(results_dir, rpm))
            subprocess.check_call(cmd, shell=True)
    except Exception as e:
        util.print_fatal("Error extracting RPMS: {}".format(e))

    os.chdir(download_path)
    collected_files = set()

    # Places we expect to find shared libraries
    for check_path in valid_dirs:
        if check_path[0] == '/':
            check_path = check_path[1:]

        dirn = os.path.join(extract_dir, check_path)
        if not os.path.isdir(dirn):
            continue

        for file in os.listdir(dirn):
            f = os.path.basename(file)

            clean_path = os.path.abspath(os.path.join(dirn, f))
            if not is_file_valid(clean_path):
                continue
            collected_files.add(clean_path)

    abi_report = dict()

    # Now examine these libraries
    for library in sorted(collected_files):
        soname = get_soname(library)
        if not soname:
            util.print_fatal("Failed to determine soname of valid library!")
            sys.exit(1)
        symbols = dump_symbols(library)
        if symbols and len(symbols) > 0:
            if soname not in abi_report:
                abi_report[soname] = set()
            abi_report[soname].update(symbols)

    report_file = os.path.join(download_path, "symbols")

    if len(abi_report) > 0:
        # Finally, write the report
        report = open(report_file, "w")
        for soname in sorted(abi_report.keys()):
            for symbol in sorted(abi_report[soname]):
                report.write("{}:{}\n".format(soname, symbol))

        report.close()
    else:
        truncate_file(report_file)

    os.chdir(old_dir)
    purge_tree(extract_dir)