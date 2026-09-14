# Spiral_Learning_Interligence_System

## WhatsApp feature flag

WhatsApp webhook processing and outbound Cloud API sends are disabled by default. Set
`WHATSAPP_ENABLED=true` (also accepts `1`, `yes`, or `on`, case-insensitively) to enable
the existing integration. When disabled, Meta webhook verification remains available
and valid JSON POST events are acknowledged without being stored or processed.
