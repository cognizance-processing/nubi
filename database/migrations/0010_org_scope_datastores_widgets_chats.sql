-- Add organization_id to datastores, widgets, and chats
-- so switching orgs actually scopes all data, not just boards.

-- Datastores
ALTER TABLE public.datastores
    ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_datastores_org_id
    ON public.datastores (organization_id);

-- Widgets
ALTER TABLE public.widgets
    ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_widgets_org_id
    ON public.widgets (organization_id);

-- Chats (standalone chats not linked to a board)
ALTER TABLE public.chats
    ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_chats_org_id
    ON public.chats (organization_id);

-- Backfill: assign existing rows to the user's first org
UPDATE public.datastores d
SET organization_id = (
    SELECT om.organization_id FROM organization_members om
    WHERE om.user_id = d.user_id ORDER BY om.organization_id LIMIT 1
)
WHERE d.organization_id IS NULL;

UPDATE public.widgets w
SET organization_id = (
    SELECT om.organization_id FROM organization_members om
    WHERE om.user_id = w.user_id ORDER BY om.organization_id LIMIT 1
)
WHERE w.organization_id IS NULL;

UPDATE public.chats c
SET organization_id = COALESCE(
    (SELECT b.organization_id FROM boards b WHERE b.id = c.board_id),
    (SELECT om.organization_id FROM organization_members om
     WHERE om.user_id = c.user_id ORDER BY om.organization_id LIMIT 1)
)
WHERE c.organization_id IS NULL;
