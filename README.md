# fpp-plugin-naughtynice

FPP plugin for [NaughtyNice Cloud](https://github.com/jjarboe01/naughtynice-cloud).

Runs a poll daemon on your FPP box that connects **outbound** to the cloud
service with your show token, pulls new NaughtyNice list submissions, and
triggers the display sequence via the local FPP API. No port forwarding,
no inbound firewall rules, works behind CGNAT.

## Install

FPP UI -> Content Setup -> Plugin Manager -> install by URL:

    https://github.com/jjarboe01/fpp-plugin-naughtynice.git

Then open the plugin page and paste in your show token.

## Structure

    plugin_setup.php   FPP UI page: token, poll interval, status
    daemon/            poll daemon (Python)
    scripts/           fpp_install/uninstall, postStart/preStop hooks
