/* Same spellings with incompatible types are not protocol contracts. */
typedef struct { int (*processingFunc)(int); } wrong_elt_t;
static wrong_elt_t elt;
static int other(int x) { return x; }
wrong_elt_t *newNotifiedFIFO_elt(int a, int b, void *c, int (*f)(int)) { return &elt; }
void pushTpool(int x) { (void)x; }
int main(void) {
  wrong_elt_t *e = newNotifiedFIFO_elt(0, 0, 0, other);
  e->processingFunc(0);
  pushTpool(0);
  return 0;
}
