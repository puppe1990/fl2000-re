"""Register map invariants. A typo here fails at runtime on the dongle, not at import."""

from fl2000_re import registers


def test_usb_ids_are_the_hagibis_dongle():
    assert (registers.FL2000_USB_VID, registers.FL2000_USB_PID) == (0x1D5C, 0x2000)


def test_vendor_request_codes_and_direction_bits():
    assert (registers.REQ_READ, registers.REQ_WRITE) == (64, 65)
    assert registers.RT_IN == 0xC0
    assert registers.RT_OUT == 0x40


def test_mmio_offsets_are_unique_and_dword_aligned():
    offsets = [
        registers.REG_STATUS,
        registers.REG_PXCLK,
        registers.REG_HSYNC1,
        registers.REG_HSYNC2,
        registers.REG_VSYNC1,
        registers.REG_VSYNC2,
        registers.REG_ISOCH,
        registers.REG_I2C_CTRL,
        registers.REG_I2C_RDATA,
        registers.REG_I2C_WDATA,
        registers.REG_PLL,
        registers.REG_ACLK,
        registers.REG_RST,
        registers.REG_CTRL3,
    ]
    assert len(offsets) == len(set(offsets))
    assert all(offset % 4 == 0 for offset in offsets)


def test_vga_registers_live_in_the_0x8000_window():
    for offset in (
        registers.REG_STATUS,
        registers.REG_PXCLK,
        registers.REG_PLL,
        registers.REG_ACLK,
    ):
        assert 0x8000 <= offset < 0x8090


def test_i2c_addresses_are_7bit():
    for addr in (registers.I2C_ITE, registers.I2C_DDC):
        assert 0 <= addr <= 0x7F
    assert (registers.I2C_ITE, registers.I2C_DDC) == (0x4C, 0x50)


def test_it66121_vendor_and_device_ids():
    assert (registers.ITE_VENDOR, registers.ITE_DEVICE) == (0x4954, 0x612)


def test_dump_regs_covers_usb_and_vga_window():
    dumped = set(registers.DUMP_REGS)
    assert {0x0070, 0x0078} <= dumped
    assert all(offset in dumped for offset in range(0x8000, 0x8090, 4))
    assert len(registers.DUMP_REGS) == len(dumped)


def test_bulk_endpoint_is_out_ep1():
    assert registers.BULK_EP == 0x01
