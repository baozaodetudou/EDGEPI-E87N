/* Host-only test harness. Never opens a block device or requests write access. */
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "untar.h"

int main(int argc, char **argv)
{
    struct stat info;
    const void *kernel = NULL, *root = NULL;
    size_t kernel_size = 0, root_size = 0;
    int result = 1;
    if (argc != 2) return 2;
    int fd = open(argv[1], O_RDONLY | O_NOFOLLOW);
    if (fd < 0) return 3;
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) || info.st_size < 1024 ||
        info.st_size > 768LL * 1024 * 1024) goto close_fd;
    const unsigned char *data = mmap(NULL, info.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
    if (data == MAP_FAILED) goto close_fd;
    if (!parse_tar_image(data, info.st_size, &kernel, &kernel_size, &root, &root_size)) {
        printf("%zu %zu %zu %zu\n", (const unsigned char *)kernel - data, kernel_size,
               (const unsigned char *)root - data, root_size);
        result = 0;
    }
    munmap((void *)data, info.st_size);
close_fd:
    close(fd);
    return result;
}
