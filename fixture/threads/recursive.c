/* Same spelling and arity, incompatible prototype: not the OAI API. */
void threadCreate(int a, int b, int c, int d, int e, int f) {
  (void)a; (void)b; (void)c; (void)d; (void)e; (void)f;
}
static void recurse(int n) { if (n) recurse(n - 1); }
static int check(void) { return 0; }
int main(int argc, char **argv) {
  (void)argv;
  threadCreate(0, 0, 0, 0, 0, 0);
  if (argc > 1) { int value = check(); (void)value; }
  argc > 2 && check();
  recurse(argc);
  return 0;
}
