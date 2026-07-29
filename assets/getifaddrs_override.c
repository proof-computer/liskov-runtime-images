// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 PROOF Computer

#include <errno.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static void release_interface(struct ifaddrs *interface) {
    if (interface == NULL) {
        return;
    }
    free(interface->ifa_name);
    free(interface->ifa_addr);
    free(interface->ifa_netmask);
    free(interface);
}

__attribute__((visibility("default")))
int getifaddrs(struct ifaddrs **interfaces) {
    if (interfaces == NULL) {
        errno = EINVAL;
        return -1;
    }
    *interfaces = NULL;

    struct ifaddrs *loopback = calloc(1, sizeof(*loopback));
    struct sockaddr_in *address = calloc(1, sizeof(*address));
    struct sockaddr_in *netmask = calloc(1, sizeof(*netmask));
    if (loopback == NULL || address == NULL || netmask == NULL) {
        free(address);
        free(netmask);
        release_interface(loopback);
        errno = ENOMEM;
        return -1;
    }

    loopback->ifa_name = strdup("lo");
    if (loopback->ifa_name == NULL) {
        free(address);
        free(netmask);
        release_interface(loopback);
        errno = ENOMEM;
        return -1;
    }
    loopback->ifa_flags = IFF_UP | IFF_RUNNING | IFF_LOOPBACK;

    address->sin_family = AF_INET;
    address->sin_addr.s_addr = htonl(UINT32_C(0x7f000001));
    loopback->ifa_addr = (struct sockaddr *)address;

    netmask->sin_family = AF_INET;
    netmask->sin_addr.s_addr = htonl(UINT32_C(0xff000000));
    loopback->ifa_netmask = (struct sockaddr *)netmask;

    *interfaces = loopback;
    return 0;
}

__attribute__((visibility("default")))
void freeifaddrs(struct ifaddrs *interfaces) {
    while (interfaces != NULL) {
        struct ifaddrs *next = interfaces->ifa_next;
        release_interface(interfaces);
        interfaces = next;
    }
}
