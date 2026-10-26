#import <Foundation/Foundation.h>

// Keep the token in the USB process: an activity in the display helper cannot
// protect the feeder from App Nap or timer/I/O deferral.
void *fl2000_begin_video_activity(void) {
    @autoreleasepool {
        NSActivityOptions options = NSActivityUserInitiatedAllowingIdleSystemSleep |
                                    NSActivityLatencyCritical;
        id activity = [[NSProcessInfo processInfo] beginActivityWithOptions:options
            reason:@"Continuous FL2000 USB display video"];
        return (__bridge_retained void *)activity;
    }
}

void fl2000_end_video_activity(void *token) {
    if (!token) return;
    @autoreleasepool {
        id activity = (__bridge_transfer id)token;
        [[NSProcessInfo processInfo] endActivity:activity];
    }
}
