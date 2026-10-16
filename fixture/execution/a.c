/* Identity fixture only; execution relations are declared by the test fixture. */
static int handler(int value) {
#ifdef FAST
  return value + 1;
#else
  return value * 2;
#endif
}
int run_a(int value) { return handler(value); }
