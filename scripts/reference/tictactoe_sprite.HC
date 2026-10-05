// Scripted reference: create a native sprite and edit a saved copy of the game.
// Run these commands in the TempleOS shell; the bundled source is untouched.
DirMk("B:/Home");
CDC *SPCanvas=DCNew(32,32);
DCFill(SPCanvas,TRANSPARENT);
SPCanvas->color=BLACK;
GrRect(SPCanvas,2,2,28,28);
SPCanvas->color=YELLOW;
GrRect(SPCanvas,4,4,24,24);
SPCanvas->color=BLACK;
GrRect(SPCanvas,8,9,5,5);
GrRect(SPCanvas,19,9,5,5);
GrRect(SPCanvas,9,21,14,4);
U8 *SPRaw=DC2Sprite(SPCanvas);
FileWrite("B:/Home/RobotSprite.SP",SPRaw,SpriteSize(SPRaw));
DCDel(SPCanvas);
Free(SPRaw);

U8 *SPSource=FileRead("::/Demo/Games/TicTacToe.HC.Z");
U8 *SPOriginal=StrNew(SPSource);
U8 *SPBegin=StrFind("U0 DrawX",SPSource);
U8 *SPEnd=StrFind("U0 DrawO",SPBegin);
*SPBegin=0;
U8 *SPOutput=MStrPrint("%s%s%s\nFree(RobotSprite);\n",SPSource,
  "U8 *RobotSprite=FileRead(\"B:/Home/RobotSprite.SP\");\n"
  "U0 DrawX(CDC *dc,I64 x,I64 y) { Sprite3(dc,x-16,y-16,0,RobotSprite); }\n\n",
  SPEnd);
FileWrite("B:/Home/RobotTicTacToe.HC",SPOutput,StrLen(SPOutput));
U8 *SPAgain=FileRead("::/Demo/Games/TicTacToe.HC.Z");
Print("ORIGINAL_UNCHANGED=%d\n",!StrCmp(SPOriginal,SPAgain));
Free(SPAgain);
Free(SPOriginal);
Free(SPSource);
Free(SPOutput);

// Launch the saved game. Stock exit is CTRL-ALT-c.
#include "B:/Home/RobotTicTacToe.HC"
