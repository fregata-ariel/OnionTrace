/* Same internal name in a different translation unit. */
static int handler(int value) { return value - 1; }
int run_b(int value) { return handler(value); }
