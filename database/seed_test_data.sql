-- Seed Test Data for Multi-Agent AI System
-- This inserts initial test data for development and testing

-- Insert a test CEO context
INSERT INTO ceo_context (name, company, output_style, strategic_priorities, known_constraints)
VALUES (
    'Alex Chen',
    'TechStartup Inc',
    'Direct and action-oriented. Prefers bullet points over paragraphs. Focus on ROI and timelines.',
    'Product-market fit, customer acquisition, sustainable growth, team building',
    'Limited runway (12 months), small team (5 people), no external funding yet'
)
ON CONFLICT DO NOTHING;

-- Note: Other tables will be populated dynamically during operation
-- Messages, sessions, events, etc. are created by the application
