"""Minimal AXI-Lite master BFM for cocotb.

Supports 32-bit data, up to 32-bit address, per-byte wstrb. One outstanding
transaction at a time (matches the regfile subordinate). Returns the BRESP /
RRESP so tests can check for SLVERR on invalid addresses.
"""

from __future__ import annotations

from dataclasses import dataclass

from cocotb.triggers import RisingEdge


@dataclass
class AxilBus:
    awaddr: object
    awvalid: object
    awready: object
    wdata: object
    wstrb: object
    wvalid: object
    wready: object
    bresp: object
    bvalid: object
    bready: object
    araddr: object
    arvalid: object
    arready: object
    rdata: object
    rresp: object
    rvalid: object
    rready: object


class AxilMaster:
    def __init__(self, bus: AxilBus, clk):
        self.bus = bus
        self.clk = clk
        # Idle all master-driven signals.
        self.bus.awvalid.value = 0
        self.bus.wvalid.value = 0
        self.bus.bready.value = 0
        self.bus.arvalid.value = 0
        self.bus.rready.value = 0
        self.bus.awaddr.value = 0
        self.bus.wdata.value = 0
        self.bus.wstrb.value = 0
        self.bus.araddr.value = 0

    async def write(self, addr: int, data: int, strb: int = 0xF) -> int:
        """Drive one AW+W beat, wait for B, return BRESP (0=OKAY, 2=SLVERR)."""
        self.bus.awaddr.value = addr
        self.bus.wdata.value = data
        self.bus.wstrb.value = strb
        self.bus.awvalid.value = 1
        self.bus.wvalid.value = 1
        self.bus.bready.value = 1
        # Wait until both AW and W are accepted.
        while True:
            await RisingEdge(self.clk)
            aw_done = int(self.bus.awready.value) == 1
            w_done = int(self.bus.wready.value) == 1
            if aw_done:
                self.bus.awvalid.value = 0
            if w_done:
                self.bus.wvalid.value = 0
            if aw_done and w_done:
                break
        # Wait for BVALID.
        while int(self.bus.bvalid.value) == 0:
            await RisingEdge(self.clk)
        bresp = int(self.bus.bresp.value)
        self.bus.bready.value = 0
        return bresp

    async def read(self, addr: int) -> tuple[int, int]:
        """Drive AR, wait for R, return (rdata, rresp)."""
        self.bus.araddr.value = addr
        self.bus.arvalid.value = 1
        self.bus.rready.value = 1
        while True:
            await RisingEdge(self.clk)
            if int(self.bus.arready.value) == 1:
                self.bus.arvalid.value = 0
                break
        while int(self.bus.rvalid.value) == 0:
            await RisingEdge(self.clk)
        rdata = int(self.bus.rdata.value)
        rresp = int(self.bus.rresp.value)
        self.bus.rready.value = 0
        return rdata, rresp
