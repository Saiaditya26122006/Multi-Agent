-- Prosody XMPP Server Configuration for Phase 2 Multi-Agent System
-- Copy to /etc/prosody/prosody.cfg.lua with:
--   sudo cp config/phase2/prosody.cfg.lua /etc/prosody/prosody.cfg.lua

admins = { "mother_agent@localhost" }

modules_enabled = {
    "roster";
    "saslauth";
    "tls";
    "dialback";
    "disco";
    "posix";
    "register";
    "admin_adhoc";
    "blocklist";
    "carbons";
    "csi";
    "ping";
    "time";
    "uptime";
    "version";
}

allow_registration = true
c2s_require_encryption = false
s2s_require_encryption = false

authentication = "internal_plain"

log = {
    info = "/var/log/prosody/prosody.log";
    error = "/var/log/prosody/prosody.err";
}

VirtualHost "localhost"
    allow_registration = true
    ssl = {
        certificate = "/etc/prosody/certs/localhost.crt";
        key = "/etc/prosody/certs/localhost.key";
    }
