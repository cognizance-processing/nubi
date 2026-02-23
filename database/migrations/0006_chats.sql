-- Chats: linked to boards only
CREATE TABLE IF NOT EXISTS public.chats (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE NOT NULL,
    board_id UUID REFERENCES public.boards(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New chat',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_chats_user_id
    ON public.chats (user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_chats_board_id
    ON public.chats (board_id, updated_at DESC);

-- Chat messages
CREATE TABLE IF NOT EXISTS public.chat_messages (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    chat_id UUID REFERENCES public.chats(id) ON DELETE CASCADE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_created
    ON public.chat_messages (chat_id, created_at ASC);

-- Trigger to bump chats.updated_at when a message is inserted
CREATE OR REPLACE FUNCTION public.bump_chat_updated_at()
RETURNS trigger AS $$
BEGIN
    UPDATE public.chats SET updated_at = timezone('utc'::text, now()) WHERE id = new.chat_id;
    RETURN new;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER chat_messages_updated_at
    AFTER INSERT ON public.chat_messages
    FOR EACH ROW EXECUTE FUNCTION public.bump_chat_updated_at();
