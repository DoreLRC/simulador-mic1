"""Via de dados: latches, AMUX, MAR/MBR, barramento C com ENC e a ordem Δx → Δy → Δz."""

import pytest

from mic1.datapath import ALUOp, Datapath, DatapathControl, Reg, RegisterFile, Shift


def test_delta_x_puts_two_registers_on_the_buses_and_latches_them():
    dp = Datapath(RegisterFile(ac=7, sp=9))
    dp.load_latches(DatapathControl(a=Reg.AC, b=Reg.SP))
    assert (dp.a_source, dp.a_bus, dp.latch_a) == (Reg.AC, 7, 7)
    assert (dp.b_source, dp.b_bus, dp.latch_b) == (Reg.SP, 9, 9)


def test_pc_plus_one_reads_before_writing():
    """Aula 10: os latches quebram o ciclo do fluxo de dados (sem condição de corrida)."""
    dp = Datapath(RegisterFile(pc=41))
    dp.execute(DatapathControl(a=Reg.PLUS_ONE, b=Reg.PC, alu=ALUOp.ADD, enc=1, c=Reg.PC))
    assert dp.registers[Reg.PC] == 42


def test_alu_works_on_the_latched_values():
    control = DatapathControl(a=Reg.AC, b=Reg.AC, alu=ALUOp.ADD, enc=1, c=Reg.AC)
    dp = Datapath(RegisterFile(ac=1))
    dp.load_latches(control)
    dp.registers[Reg.AC] = 100  # mudar o registrador depois do Δx não afeta este ciclo
    dp.compute(control)
    assert dp.alu_out == 2


def test_amux_feeds_the_mbr_into_the_alu():
    """Microinstrução 14: ``ac := mbr + ac``."""
    dp = Datapath(RegisterFile(ac=5))
    dp.load_mbr_from_memory(0x30)
    dp.execute(DatapathControl(amux=1, b=Reg.AC, alu=ALUOp.ADD, enc=1, c=Reg.AC))
    assert dp.amux_from_mbr and dp.amux_out == 0x30
    assert dp.registers[Reg.AC] == 0x35


def test_flags_come_from_the_alu_not_from_the_shifter():
    """Microinstrução 3: ``tir := lshift(ir + ir)`` — N testa o bit 14 do IR."""
    dp = Datapath(RegisterFile(ir=0x4000))
    dp.execute(DatapathControl(a=Reg.IR, b=Reg.IR, alu=ALUOp.ADD, sh=Shift.LEFT, enc=1, c=Reg.TIR))
    assert dp.alu_out == 0x8000 and dp.n and not dp.z
    assert dp.registers[Reg.TIR] == 0x0000  # o deslocador já empurrou o bit para fora


def test_mar_loads_12_bits_from_latch_b_and_remembers_the_source():
    dp = Datapath(RegisterFile(ir=0x2005, pc=7))
    dp.execute(DatapathControl(b=Reg.IR, mar=1))  # mar := ir
    assert (dp.mar, dp.mar_source, dp.mar_from_pc) == (0x005, Reg.IR, False)

    dp.execute(DatapathControl(b=Reg.PC, mar=1))  # mar := pc
    assert (dp.mar, dp.mar_source, dp.mar_from_pc) == (7, Reg.PC, True)


def test_mar_and_its_source_persist_when_mar_is_not_loaded():
    dp = Datapath(RegisterFile(pc=3, sp=9))
    dp.execute(DatapathControl(b=Reg.PC, mar=1))
    dp.execute(DatapathControl(b=Reg.SP))  # MAR = 0: o MAR não muda
    assert (dp.mar, dp.mar_source) == (3, Reg.PC)


def test_enc_gates_the_c_bus():
    dp = Datapath(RegisterFile(ac=3))
    dp.execute(DatapathControl(a=Reg.AC, alu=ALUOp.PASS_A, c=Reg.TIR))  # ENC = 0
    assert dp.c_bus == 3 and dp.c_target is None
    assert dp.registers[Reg.TIR] == 0


def test_mbr_receives_the_shifter_output():
    """Microinstrução 9: ``mar := ir; mbr := ac; wr``."""
    dp = Datapath(RegisterFile(ac=0x1234, ir=0x1010))
    dp.execute(DatapathControl(a=Reg.AC, b=Reg.IR, alu=ALUOp.PASS_A, mbr=1, mar=1))
    assert dp.mbr == 0x1234
    assert dp.mar == 0x010


def test_writing_a_constant_through_the_c_bus_is_an_error():
    with pytest.raises(ValueError, match="constante"):
        Datapath().execute(DatapathControl(enc=1, c=Reg.ZERO))


def test_memory_hook_loads_16_bits_into_mbr():
    dp = Datapath()
    dp.load_mbr_from_memory(0x1_ABCD)
    assert dp.mbr == 0xABCD


def test_control_fields_are_validated():
    with pytest.raises(ValueError):
        DatapathControl(a=16)
    with pytest.raises(ValueError):
        DatapathControl(sh=3)


def test_reset_clears_registers_latches_and_flags():
    dp = Datapath(RegisterFile(ac=5))
    dp.execute(DatapathControl(a=Reg.MINUS_ONE, alu=ALUOp.PASS_A, enc=1, c=Reg.AC, mbr=1))
    dp.reset()
    assert dp.registers.snapshot() == RegisterFile().snapshot()
    assert (dp.latch_a, dp.mbr, dp.mar, dp.mar_source, dp.n, dp.z) == (0, 0, 0, None, False, False)
