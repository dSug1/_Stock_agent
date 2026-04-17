# Module 10 — Alert dispatcher
## Purpose
Routes all system alerts to the correct delivery channel
based on priority. Receives events from Layer 4 signal generator,
Layer 0 regime classifier, and Layer 3 registry.

## Alert priority tiers

### SMS / push — must reach portfolio owner within minutes
[copy the SMS bullet list from Layer 4 section 4.5]

### Daily digest — morning before market open
[copy the daily digest bullet list from Layer 4 section 4.5]

### Weekly summary — Sunday evening
[copy the weekly summary bullet list from Layer 4 section 4.5]

## Full alert type registry
[copy the 15-row alert type table from Layer 4 section 4.5]

## Implementation inputs and outputs
- inputs: Layer 4 alert queue, Layer 0 regime change events,
          Layer 3 registry threshold events
- outputs: SMS via Twilio, email via SMTP,
           daily digest file, weekly summary file
- key constraint: SMS for regime change and all urgent alerts
                  regardless of other cadence settings
                  weekly summary includes Module 12 14-day preview