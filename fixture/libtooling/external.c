static int leaf(int value) { return value - 1; }
int external(int value) { return leaf(value); }
