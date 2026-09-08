// Creates a WindowServer extra screen via private CGVirtualDisplay.
// Must stay running: releasing the object removes the display.
//
// Build: clang -fobjc-arc -O2 -framework Foundation -framework CoreGraphics \
//        -o native/hagibis_virtual_display native/hagibis_virtual_display.m
//
// Classes are messaged through NSClassFromString so we do not link private
// symbols (same trick as hidpi-mirror / BetterDummy).

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

@interface CGVirtualDisplaySettings : NSObject
@property(retain, nonatomic) NSArray *modes;
@property(nonatomic) unsigned int hiDPI;
@end

@interface CGVirtualDisplayDescriptor : NSObject
@property(retain, nonatomic) dispatch_queue_t queue;
@property(retain, nonatomic) NSString *name;
@property(nonatomic) CGSize sizeInMillimeters;
@property(nonatomic) unsigned int maxPixelsWide;
@property(nonatomic) unsigned int maxPixelsHigh;
@property(nonatomic) CGPoint redPrimary;
@property(nonatomic) CGPoint greenPrimary;
@property(nonatomic) CGPoint bluePrimary;
@property(nonatomic) CGPoint whitePoint;
@property(copy, nonatomic) void (^terminationHandler)(id, id);
@property(nonatomic) unsigned int serialNum;
@property(nonatomic) unsigned int productID;
@property(nonatomic) unsigned int vendorID;
@end

@interface CGVirtualDisplayMode : NSObject
- (instancetype)initWithWidth:(unsigned int)width
                       height:(unsigned int)height
                  refreshRate:(double)refreshRate;
@end

@interface CGVirtualDisplay : NSObject
@property(readonly, nonatomic) unsigned int displayID;
- (instancetype)initWithDescriptor:(CGVirtualDisplayDescriptor *)descriptor;
- (BOOL)applySettings:(CGVirtualDisplaySettings *)settings;
@end

static CGVirtualDisplay *gVirtual = nil;

static unsigned parse_u(int argc, char **argv, const char *flag, unsigned fallback) {
    for (int i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], flag) == 0)
            return (unsigned)strtoul(argv[i + 1], NULL, 0);
    }
    return fallback;
}

static const char *parse_s(int argc, char **argv, const char *flag, const char *fallback) {
    for (int i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], flag) == 0)
            return argv[i + 1];
    }
    return fallback;
}

static void on_signal(int sig) {
    (void)sig;
    CFRunLoopStop(CFRunLoopGetMain());
}

static BOOL wait_online(CGDirectDisplayID display_id) {
    for (int attempt = 0; attempt < 50; attempt++) {
        CGDirectDisplayID ids[16];
        uint32_t n = 0;
        if (CGGetOnlineDisplayList(16, ids, &n) == kCGErrorSuccess) {
            for (uint32_t i = 0; i < n; i++) {
                if (ids[i] == display_id)
                    return YES;
            }
        }
        usleep(100000);
    }
    return NO;
}

static BOOL arrange_right_of_others(CGDirectDisplayID virtual_id) {
    CGDirectDisplayID ids[16];
    uint32_t n = 0;
    if (CGGetActiveDisplayList(16, ids, &n) != kCGErrorSuccess)
        return NO;
    CGRect union_r = CGRectZero;
    BOOL any = NO;
    for (uint32_t i = 0; i < n; i++) {
        if (ids[i] == virtual_id)
            continue;
        CGRect b = CGDisplayBounds(ids[i]);
        union_r = any ? CGRectUnion(union_r, b) : b;
        any = YES;
    }
    if (!any)
        return NO;
    int32_t x = (int32_t)CGRectGetMaxX(union_r);
    int32_t y = (int32_t)CGRectGetMinY(union_r);
    CGDisplayConfigRef cfg = NULL;
    if (CGBeginDisplayConfiguration(&cfg) != kCGErrorSuccess)
        return NO;
    CGConfigureDisplayMirrorOfDisplay(cfg, virtual_id, kCGNullDirectDisplay);
    if (CGConfigureDisplayOrigin(cfg, virtual_id, x, y) != kCGErrorSuccess) {
        CGCancelDisplayConfiguration(cfg);
        return NO;
    }
    return CGCompleteDisplayConfiguration(cfg, kCGConfigureForSession) == kCGErrorSuccess;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        unsigned width = parse_u(argc, argv, "--width", 720);
        unsigned height = parse_u(argc, argv, "--height", 480);
        unsigned vendor = parse_u(argc, argv, "--vendor", 0xF200);
        unsigned product = parse_u(argc, argv, "--product", 0x0E00);
        const char *name = parse_s(argc, argv, "--name", "Hagibis");

        Class descCls = NSClassFromString(@"CGVirtualDisplayDescriptor");
        Class dispCls = NSClassFromString(@"CGVirtualDisplay");
        Class setCls = NSClassFromString(@"CGVirtualDisplaySettings");
        Class modeCls = NSClassFromString(@"CGVirtualDisplayMode");
        if (!descCls || !dispCls || !setCls || !modeCls) {
            fprintf(stderr, "CGVirtualDisplay API unavailable\n");
            return 1;
        }

        CGVirtualDisplayDescriptor *desc = [[descCls alloc] init];
        desc.name = [NSString stringWithUTF8String:name];
        desc.queue = dispatch_get_main_queue();
        // ~110 DPI at 720x480 so menu chrome is usable, not postage-stamp.
        desc.sizeInMillimeters = CGSizeMake(166.0, 111.0);
        desc.maxPixelsWide = width > 1920 ? width : 1920;
        desc.maxPixelsHigh = height > 1080 ? height : 1080;
        desc.redPrimary = CGPointMake(0.680, 0.320);
        desc.greenPrimary = CGPointMake(0.265, 0.690);
        desc.bluePrimary = CGPointMake(0.150, 0.060);
        desc.whitePoint = CGPointMake(0.3127, 0.3290);
        desc.vendorID = vendor;
        desc.productID = product;
        desc.serialNum = 1;
        desc.terminationHandler = ^(id a, id b) {
            (void)a;
            (void)b;
            fprintf(stderr, "virtual display terminated by WindowServer\n");
            exit(0);
        };

        gVirtual = [[dispCls alloc] initWithDescriptor:desc];
        if (!gVirtual) {
            fprintf(stderr, "failed to create CGVirtualDisplay\n");
            return 1;
        }

        CGVirtualDisplaySettings *settings = [[setCls alloc] init];
        // 1x: pixels == points == HDMI 720x480. HiDPI would downsample on the Dell.
        settings.hiDPI = 0;
        settings.modes = @[ [[modeCls alloc] initWithWidth:width height:height refreshRate:60] ];
        if (![gVirtual applySettings:settings]) {
            fprintf(stderr, "CGVirtualDisplay applySettings failed\n");
            return 1;
        }

        CGDirectDisplayID display_id = gVirtual.displayID;
        if (!wait_online(display_id)) {
            fprintf(stderr, "virtual display %u did not come online\n", display_id);
            return 1;
        }
        if (!arrange_right_of_others(display_id)) {
            fprintf(stderr, "warning: could not place virtual display to the right\n");
        }

        CGRect bounds = CGDisplayBounds(display_id);
        printf("HAGIBIS_VIRTUAL display_id=%u width=%d height=%d origin_x=%d origin_y=%d\n",
               display_id,
               (int)bounds.size.width,
               (int)bounds.size.height,
               (int)bounds.origin.x,
               (int)bounds.origin.y);
        fflush(stdout);

        signal(SIGINT, on_signal);
        signal(SIGTERM, on_signal);
        CFRunLoopRun();
        gVirtual = nil;
    }
    return 0;
}
