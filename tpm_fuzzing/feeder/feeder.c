// #include <stdio.h>
// #include <stdlib.h>

// int main(int argc, char const *argv[]) {
// 	while (1) {
// 		unsigned short size;
// 		if (fread(&size, sizeof(size), 1, stdin) != 1) exit(1);
// 		size &= (1<<13)-1;
// 		{
// 			unsigned char buf[size];
// 			if (fread(buf, sizeof(buf), 1, stdin) != 1) exit(2);

// 			for (unsigned char* p = buf; p - buf < size; p++) {
// 				printf("%02x ", *p);
// 			}
// 			printf("\n");
// 		}
// 	}

// 	return 0;
// }



#define _GNU_SOURCE

#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <pthread.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
// #include <signal.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
// #include <poll.h>
#include <stdint.h>

//
// originals
//
// int (*original_socket) (int, int, int);
// int (*original_bind)   (int, const struct sockaddr *, socklen_t);
// int (*original_listen) (int, int);
// int (*original_accept) (int, struct sockaddr *, socklen_t *);
// int (*original_connect)(int sockfd, const struct sockaddr *addr, socklen_t addrlen);
int (*original_read) (int fd, void *buf, size_t count);
int (*original_write)(int fd, const void *buf, size_t count);
__attribute__((constructor)) void feeder_orig() {
// 	original_socket  = dlsym(RTLD_NEXT, "socket");
// 	original_listen  = dlsym(RTLD_NEXT, "listen");
// 	original_accept  = dlsym(RTLD_NEXT, "accept");
// 	original_bind    = dlsym(RTLD_NEXT, "bind");
// 	original_connect = dlsym(RTLD_NEXT, "connect");
	original_read  = dlsym(RTLD_NEXT, "read");
	original_write = dlsym(RTLD_NEXT, "write");
}

const size_t bufmax = 1<<18;
char buf[bufmax];
char* bufp = NULL;
size_t bufs = 0;
uint16_t size;
int8_t next_plat;

pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
pthread_cond_t cond_tpm  = PTHREAD_COND_INITIALIZER;
pthread_cond_t cond_plat = PTHREAD_COND_INITIALIZER;

#if 0
	#define LOCK do {fprintf(stderr, "\t\t\t\tt%lu tryLOCK %s:%d\n", syscall(186)%100, __FILE__, __LINE__); pthread_mutex_lock(&mutex); fprintf(stderr, "\tt%lu LOCK    %s:%d\n", syscall(186)%100, __FILE__, __LINE__);} while (0)
	#define WAIT(cond) do {fprintf(stderr, "\t\t\t\tt%lu WAIT    %s:%d\n", syscall(186)%100, __FILE__, __LINE__); pthread_cond_wait(cond, &mutex); fprintf(stderr, "\tt%lu WAITed  %s:%d\n", syscall(186)%100, __FILE__, __LINE__);} while (0)
	#define UNLOCK do {fprintf(stderr, "\tt%lu UNLOCK  %s:%d\n", syscall(186)%100, __FILE__, __LINE__); pthread_mutex_unlock(&mutex);} while (0)
#else
	#define LOCK       pthread_mutex_lock(&mutex)
	#define WAIT(cond) pthread_cond_wait(cond, &mutex)
	#define UNLOCK     pthread_mutex_unlock(&mutex)
#endif


// TODO maybe constructor
static void readbuf() {
	LOCK;
		if (bufs) {
			UNLOCK;
			return;
		}
		bufs = fread(buf, sizeof(char), bufmax, stdin);
		if (bufs < 1) exit(24);
		bufp = buf;
	UNLOCK;
}

void waitfortype(int8_t plat) {
	LOCK;
	while (1) {
		if (bufp == buf + bufs) exit(0);
		size = (*(uint16_t*) bufp);
		next_plat = !!(size & 1<<15);
		size &= (1<<13)-1;
		if (bufp + sizeof(uint16_t) + size > buf + bufs) exit(29);

		if        (!plat && next_plat) {
			// wait for tpm packet
			pthread_cond_signal(&cond_plat);
			WAIT(&cond_tpm);

		} else if (plat && !next_plat) {
			// wait fot plat packet
			pthread_cond_signal(&cond_tpm);
			WAIT(&cond_plat);

		} else if (( plat &&  next_plat) ||
				   (!plat && !next_plat)) {
			UNLOCK;
			return;
		}
	}
}

int socket(int domain, int type, int protocol) {
	if (!bufs) readbuf();

	if (domain == AF_INET6) {
		errno = EAFNOSUPPORT;
		return -1;
	
	} else {
		static int last = 10000;
		LOCK;
			last++;
		UNLOCK;
		return last-1;		
	}
}

int setsockopt(int sockfd, int level, int optname, const void *optval, socklen_t optlen) {
	return 0;
}

int tpm_service  = -1;
int plat_service = -1;


// __attribute__((constructor)) void serv_init() {
// 	tpm_service = plat_service = -1;
// }

int bind(int sockfd, const struct sockaddr* addr, socklen_t addrlen) {
	LOCK;
		short port = ntohs(((struct sockaddr_in*) addr)->sin_port);
		// printf("%d %d\n", sockfd, port);
		if (port == 2321) tpm_service  = sockfd;
		if (port == 2322) plat_service = sockfd;
	UNLOCK;
	return 0;
}

int listen(int sockfd, int backlog) {
	return 0;
}

int select(int nfds, fd_set *readfds, fd_set *writefds, fd_set *exceptfds, struct timeval *timeout) {
	int fd = nfds - 1;

	if (fd == tpm_service) {
		waitfortype(0);
	} else if (fd == plat_service) {
		waitfortype(1);
	} else {
		exit(26);
	}

	return 1;
}

int accept(int sockfd, struct sockaddr *addr, socklen_t *addrlen) {
	return sockfd + 100;
}

ssize_t read(int fd, void *rbuf, size_t count) {
	if (fd < 10000) return original_read(fd, buf, count);

	if (fd == tpm_service + 100) {
		waitfortype(0);
	} else if (fd == plat_service + 100) {
		waitfortype(1);
	} else {
		exit(27);
	}

	static int already_sent = 0;

	// fprintf(stderr, "\r read: %lu %lu", size, count);

	int to_send;
	if (count < size - already_sent) {
		to_send = count;
	} else {
		to_send = size - already_sent;
	}

	memcpy(rbuf, bufp + 2 + already_sent, to_send);
	if (already_sent + to_send == size) {
		bufp += 2;
		bufp += size;
		already_sent = 0;		
	} else {
		already_sent += to_send;
	}

	return size;
}

ssize_t write(int fd, const void *buf, size_t count) {
	if (fd < 10000) return original_write(fd, buf, count);

	return count;
}

