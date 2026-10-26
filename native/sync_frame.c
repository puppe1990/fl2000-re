#include <time.h>

typedef int (*bulk_transfer_fn)(void *, unsigned char, unsigned char *, int,
                                int *, unsigned int);

static double milliseconds(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (double)now.tv_sec * 1000.0 + (double)now.tv_nsec / 1000000.0;
}

static int timed_transfer(bulk_transfer_fn transfer, void *handle,
                          unsigned char *pixels, int length, unsigned int timeout,
                          int *written, double *elapsed) {
    *written = 0;
    double started = milliseconds();
    int status = transfer(handle, 1, pixels, length, written, timeout);
    *elapsed = milliseconds() - started;
    return status ? status : (*written != length ? -1 : 0);
}

/* Use the caller's already loaded libusb instance. Both phases are synchronous:
 * no Python transition/copy between pixels and EOF, no queue, retry or retained DMA.
 */
int fl2000_send_sync_frame(bulk_transfer_fn transfer, void *handle,
                          unsigned char *pixels, int length,
                          unsigned int pixel_timeout, unsigned int eof_timeout,
                          int *phase, int *written, double *pixel_ms, double *eof_ms) {
    *phase = 0;
    *eof_ms = 0;
    int status = timed_transfer(transfer, handle, pixels, length, pixel_timeout,
                               written, pixel_ms);
    if (status) return status;
    *phase = 1;
    return timed_transfer(transfer, handle, 0, 0, eof_timeout, written, eof_ms);
}
