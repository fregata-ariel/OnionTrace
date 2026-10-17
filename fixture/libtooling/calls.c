#define APPLY(fn, value) fn(value)
extern int external(int);
static int leaf(int value) { return value + VARIANT; }
int dispatch(int (*callback)(int), int value) {
  int result = APPLY(leaf, value);
#if VARIANT == 2
  result += external(value);
#endif
  for (int i = 0; i < value; ++i) {
    if (i & 1) result += leaf(i);
    else result += callback(i);
  }
  return result;
}
