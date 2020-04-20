int rand(void) {
	static long rand = 42;
	rand *= 2147483629l;
	rand %= 2147483647l;
	return (int) rand;
}

int rand_r(unsigned int *a) {
	return rand();
}

long random() {
	return rand();
}
