// The maze demo is taken from Felipe Andres Manzano's blog:
// http://feliam.wordpress.com/2010/10/07/the-symbolic-maze/
//
//

#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <assert.h>
#include <stdbool.h>

#define H 7
#define W 11
char maze[H][W] = { "+-+---+---+",
                    "| |     |#|",
                    "| | --+ | |",
                    "| |   | | |",
                    "| +-- | | |",
                    "|     |   |",
                    "+-----+---+" };
void draw ()
{
	int i, j;
	for (i = 0; i < H; i++)
	  {
		  for (j = 0; j < W; j++)
				  printf ("%c", maze[i][j]);
		  printf ("\n");
	  }
	printf ("\n");
}

int
main (int argc, char *argv[])
{
	int x, y;     //Player position
	int ox, oy;   //Old player position
	int i = 0;    //Iteration number
#define ITERS 512
	char program[ITERS];
	x = 1;
	y = 1;
	maze[y][x]='X';
	draw();
	read(0,program,ITERS);
	while(i < ITERS)
	{
#ifndef MAZE_NO_BT
		maze[y][x]=' ';
#endif
		ox = x;    //Save old player position
		oy = y;
    //transition(hashint(x,y));
		switch (program[i])
		{
			case 'w':
				y--;
				break;
			case 's':
				y++;
				break;
			case 'a':
				x--;
				break;
			case 'd':
				x++;
				break;
			default:
				printf("Wrong command!(only w,s,a,d accepted!)\n");
				printf("You lose!\n");
				exit(-1);
		}
		if (maze[y][x] == '#')
		{
      assert(0);
		}
		if (maze[y][x] != ' ') {
			x = ox;
			y = oy;
		}
#ifdef MAZE_NO_BT
		if (ox==x && oy==y){
			printf("You lose\n");
			exit(-2);
		}
#endif

		maze[y][x]='X';
		draw ();          //draw it
		i++;
	}
	printf("You lose\n");
}
