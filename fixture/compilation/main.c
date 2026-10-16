#include "config.h"
int shared(void);
int main(void) { return shared() == VARIANT ? 0 : 1; }
