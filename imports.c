
static void sync_fuzzers(char** argv) {

  DIR* sd;
  struct dirent* sd_ent;
  u32 sync_cnt = 0;

  sd = opendir(import_dir);
  if (!sd) PFATAL("Unable to open '%s'", import_dir);

  stage_max = stage_cur = 0;
  cur_depth = 0;

  /* Look at the entries created for every other fuzzer in the sync directory. */

  while ((sd_ent = readdir(sd))) {

    static u8 stage_tmp[128];

    struct dirent* qd_ent;
    u8 *import_path;
    u32 min_accept = 0, next_min_accept;

    /* Skip anything that doesn't have a queue/ subdirectory. */

    import_path = alloc_printf("%s/%s", import_dir, sd_ent->d_name);

    s32 fd;
    struct stat st;

    fd = open(import_path, O_RDONLY);

    if (fd < 0) {
       ck_free(path);
       continue;
    }

    if (fstat(fd, &st)) PFATAL("fstat() failed");

    /* Ignore zero-sized or oversized files. */

    if (st.st_size && st.st_size <= MAX_FILE) {

      u8  fault;
      u8* mem = mmap(0, st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);

      if (mem == MAP_FAILED) PFATAL("Unable to mmap '%s'", path);

      /* See what happens. We rely on save_if_interesting() to catch major
         errors and save the test case. */

      write_to_testcase(mem, st.st_size);

      fault = run_target(argv, exec_tmout);

      syncing_party = sd_ent->d_name;
      queued_imported += save_if_interesting(argv, mem, st.st_size, fault);
      syncing_party = 0;

      munmap(mem, st.st_size);

      if (!(stage_cur++ % stats_update_freq)) show_stats();
    }

    remove(import_path);
    ck_free(import_path);
    close(fd);
  }
  closedir(sd);
}
