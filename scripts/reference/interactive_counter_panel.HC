I64 PanelCount=0;
Bool PanelDown=FALSE;
I64 PanelInhibit=Fs->win_inhibit;

U0 CounterPanel(CTask *task,CDC *dc)
{
  I64 x=ms.pos.x-task->pix_left-task->scroll_x;
  I64 y=ms.pos.y-task->pix_top-task->scroll_y;
  if (sys_focus_task==task && ms.lb && !PanelDown && 140<=y<200) {
    if (40<=x<180) PanelCount++;
    else if (220<=x<360) PanelCount--;
    else if (400<=x<540) PanelCount=0;
  }
  PanelDown=ms.lb;
  dc->color=WHITE;
  GrRect(dc,0,0,624,456);
  dc->color=BLACK;
  GrPrint(dc,40,32,"LIVE COUNTER PANEL");
  GrPrint(dc,40,72,"COUNT=%d",PanelCount);
  dc->color=LTGRAY;
  GrRect(dc,40,140,140,60);
  GrRect(dc,220,140,140,60);
  GrRect(dc,400,140,140,60);
  dc->color=BLACK;
  GrPrint(dc,96,160,"+1");
  GrPrint(dc,280,160,"-1");
  GrPrint(dc,448,160,"RESET");
}

DocClear;
Fs->win_inhibit|=WIF_SELF_MS_L;
Fs->draw_it=&CounterPanel;
