# Whiteboard development notes

- The existing project's actual ClaudeHub-compatible text key is configured
  through `XAI_API_KEY`; checking only HUB/ANTHROPIC names is incomplete. Use
  the project's Settings object and print only presence booleans.
- Image-provider defaults can differ from the PNG contract. Request PNG
  explicitly and verify model capabilities and actual returned dimensions.
- Successful request submission does not prove the response or charge was
  received. Persist a running marker before an API call and block uncertain
  repeats; completed bytes are reusable only after their digest is verified.
- Audio timing must come from decoded samples, not guessed text length/pauses.
- Rectangles spanning different spoken beats can reveal later material early.
  The marker-sweep pilot rejects those overlaps.
- A valid synthetic-tone MP4 is a technical test; it does not verify Russian
  narration, scene illustration quality or factual accuracy.
- Keep the pre-existing Codespace working tree separate from video development.
  Use `/workspaces/servgo-animator` and the wip branch; never commit unrelated
  server/deployment scratch files from the original workspace.
