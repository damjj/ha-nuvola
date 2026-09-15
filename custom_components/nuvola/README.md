# Nuvola Registro Elettronico — Home Assistant

Custom integration for Nuvola Registro Elettronico (Madisoft).

## Version 0.5.0

Complete package containing:

- Home Assistant config flow
- Current Nuvola authentication-service/OIDC attempt
- Session cookie handling
- API bearer-token handling
- Student discovery
- Periods
- Grades
- Absences
- Notes
- Homework
- Coordinator-based polling
- Sensors for student/grade/absence/homework/note counts
- Italian and English translations
- HACS metadata

The official Madisoft API documentation confirms that API requests require a token obtained from the authentication service. The student API endpoints used by this integration are based on the documented/reverse-engineered Nuvola student API used by existing open-source clients.

### Security

Passwords and bearer tokens are never written to the Home Assistant log.

### Known limitation

SPID/CIE/MFA/CAPTCHA or another fully interactive identity-provider flow cannot be completed automatically by a background Home Assistant integration. Normal username/password authentication can be attempted when Nuvola exposes that form through its current authentication service.

### Installation

Remove the old `/config/custom_components/nuvola` directory completely, copy the `custom_components/nuvola` directory from this ZIP into `/config/custom_components/`, restart Home Assistant, and add **Nuvola Registro Elettronico** from Settings → Devices & services.

### Debug

For diagnostics add:

```yaml
logger:
  logs:
    custom_components.nuvola: debug
```

Then restart Home Assistant and repeat the integration setup. The log records HTTP status, redirect hosts and safe response snippets, never passwords or tokens.
