"""
CEO Data Injection Layer.

Place Alex's documents here as structured JSON or plain text files.
The Mother Agent reads all files in this directory and injects relevant
context into each agent's input_package.

File naming convention:
  - financials.json — existing financial data, revenue history, projections
  - customers.json — customer research, interviews, survey results
  - competitors.json — competitive analysis, pricing intel
  - deck.txt — pitch deck content or executive notes
  - constraints.json — hard constraints Alex wants enforced (budget cap, timeline, etc.)

Files are read at pipeline start and matched to relevant sections.
"""
