# Evidence report visual review checklist

Complete this review before the proposed `0.3.0` release and before publishing
any report. Use a disposable, publication-reviewed export; do not edit evidence
to manufacture examples.

- Open `index.html` through both `file://` and a simple local HTTP server.
- Check the dashboard in System default, Light and Dark modes.
- Navigate to a verified semantic change page and back.
- Inspect a raw-only row, Initial capture, failed poll with a long error
  tooltip, large diff, hashes, commands, annotations and timestamp status.
- Use only the keyboard to reach the selector, links, details and downloads;
  confirm the focus ring remains visible in both palettes.
- Reload and navigate between root and nested pages to confirm preference
  persistence.
- While System default is selected, change the operating-system theme and
  confirm the report follows it.
- Check a narrow/mobile viewport and browser zoom at 200% for clipping and
  horizontal access to tables.
- Check print preview from both screen themes; output must use the light print
  palette and remain legible.
- Confirm raw-response links download `.txt` files rather than embedding or
  rendering archived HTML.

Automated tests cover markup, CSP, theme state transitions, local links,
encoding, archive immutability and print/theme CSS declarations. They do not
replace this human review of rendering, contrast perception and interaction.
