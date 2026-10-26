/* Independent exact RGB565 -> FL2000 RGB888/RLE codec. No USB access. */
#include <stddef.h>
#include <stdint.h>

static uint16_t source_pixel(const uint8_t *src, size_t i, size_t count) {
    /* Input frames swap 32-bit lanes in each complete 64-bit block. */
    size_t word = i < (count / 4) * 4 ? (i ^ 2) : i;
    return (uint16_t)(src[word * 2] | ((uint16_t)src[word * 2 + 1] << 8));
}

static void token(uint8_t *out, size_t *offset, uint32_t value) {
    out[(*offset)++] = (uint8_t)value;
    out[(*offset)++] = (uint8_t)(value >> 8);
    out[(*offset)++] = (uint8_t)(value >> 16);
}

static uint32_t literal(uint16_t pixel) {
    uint32_t r = pixel >> 11, g = (pixel >> 5) & 63, b = pixel & 31;
    r = (r << 3) | (r >> 2);
    g = (g << 2) | (g >> 4);
    b = (b << 3) | (b >> 2);
    return 0x800000 | ((r >> 1) << 16) | (g << 8) | b;
}

size_t fl2000_encode_rle24(const uint8_t *src, size_t count,
                          uint8_t *out, size_t capacity) {
    if (!src || !out || !count || count > 0x800000 || capacity < count * 3 + 4)
        return 0;
    size_t offset = 0, start = 0;
    while (start < count) {
        uint16_t color = source_pixel(src, start, count);
        size_t end = start + 1;
        while (end < count && source_pixel(src, end, count) == color)
            ++end;
        uint32_t value = literal(color);
        token(out, &offset, value);
        if (end - start > 1)
            token(out, &offset, end - start == 2 ? value : (uint32_t)(end - start - 1));
        start = end;
    }
    while (offset % 4)
        out[offset++] = 0x80;
    for (size_t i = 0; i + 8 <= offset; i += 8) {
        for (size_t j = 0; j < 4; ++j) {
            uint8_t byte = out[i + j];
            out[i + j] = out[i + 4 + j];
            out[i + 4 + j] = byte;
        }
    }
    return offset;
}
