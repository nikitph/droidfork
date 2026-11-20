// Fixed-purpose relay between the emulator's explicitly allowlisted Unix pipe
// and the benchmark accessibility service in the saved Android guest.
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/system_properties.h>
#include <sys/un.h>
#include <unistd.h>

static bool send_all(int fd, const void *data, size_t size) {
    const char *p = data;
    while (size) {
        ssize_t n = write(fd, p, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        p += n;
        size -= (size_t)n;
    }
    return true;
}

static void tap(int pipefd, uint32_t point[2]) {
    char marker[PROP_VALUE_MAX];
    int size = __system_property_get("debug.droidfork.node", marker);
    if (size <= 0) return;
    uint32_t length = htonl((uint32_t)size);
    if (!send_all(pipefd,&length,sizeof(length)) || !send_all(pipefd,marker,size)) return;
    int fd = socket(AF_INET,SOCK_STREAM|SOCK_CLOEXEC,0);
    if (fd < 0) return;
    struct sockaddr_in address = {.sin_family=AF_INET, .sin_port=htons(32761),
                                  .sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    unsigned char key[32];
    int secret = open("/data/local/tmp/df-observer-key", O_RDONLY | O_CLOEXEC);
    if (secret < 0) { close(fd); return; }
    ssize_t got = read(secret, key, sizeof(key));
    close(secret);
    if (got == sizeof(key) && connect(fd,(struct sockaddr *)&address,sizeof(address))==0 &&
        send_all(fd,key,sizeof(key)) &&
        send_all(fd,point,2*sizeof(*point))) {
        char buf[32768];
        ssize_t n;
        while ((n=read(fd,buf,sizeof(buf)))>0 && send_all(pipefd,buf,(size_t)n)) {}
    }
    close(fd);
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    char name[224];
    if (snprintf(name,sizeof(name),"pipe:unix:%s",argv[1]) >= (int)sizeof(name)) return 2;
    signal(SIGPIPE,SIG_IGN);
    for (;;) {
        int fd=open("/dev/goldfish_pipe_dprctd",O_RDWR|O_CLOEXEC);
        if (fd>=0) {
            char command;
            if (send_all(fd,name,strlen(name)+1) && read(fd,&command,1)==1 && (command=='T' || command=='F' || command=='O' || command=='E')) {
                uint32_t point[2];
                size_t got = 0;
                while (got < sizeof(point)) {
                    ssize_t n = read(fd, (char *)point + got, sizeof(point) - got);
                    if (n < 0 && errno == EINTR) continue;
                    if (n <= 0) break;
                    got += n;
                }
                if (got == sizeof(point) && ntohl(point[0]) < 480 && ntohl(point[1]) < 854)
                {
                    // The app receives the same point and chooses where to capture pixels.
                    if (command=='E') point[0]=htonl(ntohl(point[0]) | (1u<<28));
                    if (command=='O') point[0]=htonl(ntohl(point[0]) | (1u<<29));
                    if (command=='F') point[0]=htonl(ntohl(point[0]) | (1u<<30));
                    tap(fd,point);
                }
            }
            close(fd);
        }
        usleep(10000);
    }
}
