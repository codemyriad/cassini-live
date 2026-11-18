### Fixed
- Fixed a file-descriptor leak that stopped captions after about a week of uptime: the
  app abandoned every Nextcloud API client it opened, and their HTTP/3 connections held
  their sockets until the process ran out of file descriptors and could no longer reach
  Talk. Updating restarts the container, which releases the descriptors already held.
