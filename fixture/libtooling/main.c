extern int dispatch(int (*)(int), int);
extern int external(int);
int main(void) { return dispatch(external, 2) == 0; }
