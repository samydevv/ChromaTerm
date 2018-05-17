/* This program is protected under the GNU GPL (See COPYING) */

#include "defs.h"

static int process_already_running = FALSE;

DO_COMMAND(do_commands) {
  char buf[BUFFER_SIZE] = {0}, add[BUFFER_SIZE];
  int cmd;

  display_header(" COMMANDS ");

  for (cmd = 0; *command_table[cmd].name != 0; cmd++) {
    if (*arg && !is_abbrev(arg, command_table[cmd].name)) {
      continue;
    }

    if ((int)strlen(buf) + 20 > gts.cols) {
      display_printf(buf);
      buf[0] = 0;
    }

    sprintf(add, "%20s", command_table[cmd].name);
    strcat(buf, add);
  }
  if (buf[0]) {
    display_printf(buf);
  }

  display_header("");
}

DO_COMMAND(do_exit) {
  if (*arg) {
    quitmsg(arg, 0);
  }
  quitmsg(NULL, 0);
}

DO_COMMAND(do_help) {
  char left[BUFFER_SIZE], add[BUFFER_SIZE], buf[BUFFER_SIZE];
  int cnt;

  get_arg(arg, left);

  if (*left == 0) {
    display_header(" HELP TOPICS ");

    for (cnt = add[0] = 0; *help_table[cnt].name != 0; cnt++) {
      if ((int)strlen(add) + 19 > gts.cols) {
        display_printf(add);
        add[0] = 0;
      }
      cat_sprintf(add, "%19s", help_table[cnt].name);
    }
    display_printf(add);

    display_header("");
  } else {
    int found = FALSE;
    for (cnt = 0; *help_table[cnt].name != 0; cnt++) {
      if (is_abbrev(left, help_table[cnt].name) || is_abbrev(left, "all")) {
        found = TRUE;

        substitute(help_table[cnt].text, buf);

        display_printf(buf);
      }
    }

    if (!found) {
      display_printf("%cHELP: No help found for topic '%s'", gtd.command_char,
                     left);
    }
  }
}

DO_COMMAND(do_run) {
  char temp[BUFFER_SIZE];
  int desc, pid;
  struct winsize size;

  /* Limit to a single process */
  if (process_already_running) {
    display_printf("%cRUN: Process is already running; see %chelp run ",
                   gtd.command_char, gtd.command_char);
    return;
  } else {
    process_already_running = TRUE;
  }

  /* If it's quiet, then that must mean we're reading this run command from a
   * file; do not launch a terminal after the read finishes */
  if (gtd.quiet) {
    gtd.run_overriden = TRUE;
  }

  char *argv[4] = {"sh", "-c", "", NULL};

  /* If no process is provided, use the SHELL environment variable */
  strcpy(temp, "exec ");
  if (arg == NULL || *arg == 0) {
    strcat(temp, getenv("SHELL") ? getenv("SHELL") : "");
    strcat(temp, " -l");
  } else {
    strcat(temp, arg);
    memset(arg, 0, strlen(arg));
  }

  size.ws_row = gts.rows;
  size.ws_col = gts.cols;

  pid = forkpty(&desc, NULL, &gtd.active_terminal, &size);

  switch (pid) {
  case -1:
    perror("forkpty");
    break;
  case 0:
    argv[2] = temp;
    execv("/bin/sh", argv);
    break;
  default:
    gts.pid = pid;
    gts.socket = desc;

    if (pthread_create(&output_thread, NULL, poll_session, NULL) != 0) {
      quitmsg("failed to create input thread", 1);
    }

    break;
  }
}
