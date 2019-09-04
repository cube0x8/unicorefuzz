#!/usr/bin/env python
import argparse
import os
import signal
import sys

from unicorn import *
from unicorn.x86_const import *

import unicorefuzz.unicorefuzz
from unicorefuzz.unicorefuzz import Unicorefuzz
from unicorefuzz import utils, x64utils


class Harness(Unicorefuzz):
    pass




cs = utils.init_capstone(utils.get_arch(config.ARCH))


def main(input_file, debug=False, trace=False, wait=False):
    if wait:
        utils.wait_for_probe_wrapper()

    arch = utils.get_arch(config.ARCH)
    uc = Uc(arch.unicorn_arch, arch.unicorn_mode)

    if debug:
        # Try to load udbg
        sys.path.append(
            os.path.join(os.path.dirname(os.path.realpath(__file__)), "uDdbg")
        )
        from udbg import UnicornDbg
    if trace:
        print("[+] Settings trace hooks")
        uc.hook_add(UC_HOOK_BLOCK, unicorn_debug_block)
        uc.hook_add(UC_HOOK_CODE, unicorn_debug_instruction)
        uc.hook_add(
            UC_HOOK_MEM_WRITE | UC_HOOK_MEM_READ | UC_HOOK_MEM_FETCH,
            unicorn_debug_mem_access,
        )

    # On error: map memory.
    uc.hook_add(UC_HOOK_MEM_UNMAPPED, unicorn_debug_mem_invalid_access)

    utils.map_known_mem(uc)

    if debug or trace:
        print("[*] Reading from file {}".format(input_file))

    # we leave out gs_base and fs_base on x64 since they start the forkserver
    utils.uc_load_registers(uc)

    # let's see if the user wants a change.
    config.init_func(uc)

    # get pc from unicorn state since init_func may have altered it.
    pc = utils.uc_get_pc(uc, arch)

    # if we only have a single exit, there is no need to potentially slow down execution with an insn hook.
    if len(config.EXITS) or len(config.ENTRY_RELATIVE_EXITS):

        # add MODULE_EXITS to EXITS
        config.EXITS += [x + pc for x in config.ENTRY_RELATIVE_EXITS]
        # add final exit to EXITS
        config.EXITS.append(pc + config.LENGTH)

        if arch == unicorefuzz.unicorefuzz.X64:
            exit_hook = x64utils.init_syscall_hook(config.EXITS, os._exit)
            uc.hook_add(UC_HOOK_INSN, exit_hook, None, 1, 0, UC_X86_INS_SYSCALL)
        else:
            # TODO: (Fast) solution for X86, ARM, ...
            raise Exception("Multiple exits not yet suppored for arch {}".format(arch))

    # starts the afl forkserver
    utils.uc_start_forkserver(uc)

    input_file = open(input_file, "rb")  # load afl's input
    input = input_file.read()
    input_file.close()

    try:
        config.place_input(uc, input)
    except Exception as ex:
        print("[!] Error setting testcase for input {}: {}".format(input, ex))
        os._exit(1)

    if not debug:
        try:
            uc.emu_start(pc, pc + config.LENGTH, timeout=0, count=0)
        except UcError as e:
            print(
                "[!] Execution failed with error: {} at address {:x}".format(
                    e, utils.uc_get_pc(uc, arch)
                )
            )
            force_crash(e)
        # Exit without clean python vm shutdown: "The os._exit() function can be used if it is absolutely positively necessary to exit immediately"
        os._exit(0)
    else:
        print("[*] Starting debugger...")
        udbg = UnicornDbg()

        # TODO: Handle mappings differently? Update them at some point? + Proper exit after run?
        udbg.initialize(
            emu_instance=uc,
            entry_point=pc,
            exit_point=pc + config.LENGTH,
            hide_binary_loader=True,
            mappings=[
                (hex(x), x, unicorefuzz.unicorefuzz.PAGE_SIZE)
                for x in unicorefuzz.unicorefuzz._mapped_page_cache
            ],
        )

        def dbg_except(x, y):
            raise Exception(y)

        os.kill = dbg_except
        udbg.start()
        # TODO will never reach done, probably.
        print("[*] Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test harness for our sample kernel module"
    )
    parser.add_argument(
        "input_file",
        type=str,
        help="Path to the file containing the mutated input to load",
    )
    parser.add_argument(
        "-d",
        "--debug",
        default=False,
        action="store_true",
        help="Starts the testcase in uUdbg (if installed)",
    )
    parser.add_argument(
        "-t",
        "--trace",
        default=False,
        action="store_true",
        help="Enables debug tracing",
    )
    parser.add_argument(
        "-w",
        "--wait",
        default=False,
        action="store_true",
        help="Wait for the state directory to be present",
    )
    args = parser.parse_args()

    main(args.input_file, debug=args.debug, trace=args.trace, wait=args.wait)