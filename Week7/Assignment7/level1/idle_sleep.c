#include <stdio.h>
#include <unistd.h>

int main(void)
{
    printf("PID=%ld READY phase=idle\n", (long)getpid());
    fflush(stdout);
    sleep(30);
    return 0;
}
