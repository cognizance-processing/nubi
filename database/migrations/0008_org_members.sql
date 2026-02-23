-- Organization members: links users to organizations with roles
CREATE TABLE IF NOT EXISTS public.organization_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(organization_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_org_members_user
    ON public.organization_members (user_id);
CREATE INDEX IF NOT EXISTS idx_org_members_org
    ON public.organization_members (organization_id);

-- Replace the user-creation trigger (profile only, org created by user)
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
    INSERT INTO public.profiles (id, username, full_name, updated_at)
    VALUES (new.id, new.email, new.full_name, now());
    RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
