-- Board Queries: Python queries that belong to boards
CREATE TABLE IF NOT EXISTS public.board_queries (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    board_id UUID REFERENCES public.boards(id) ON DELETE CASCADE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    python_code TEXT NOT NULL DEFAULT '',
    ui_map JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_board_queries_board_id
    ON public.board_queries (board_id, updated_at DESC);

-- Trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION public.update_board_query_updated_at()
RETURNS trigger AS $$
BEGIN
    new.updated_at = timezone('utc'::text, now());
    RETURN new;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER board_queries_updated_at
    BEFORE UPDATE ON public.board_queries
    FOR EACH ROW EXECUTE FUNCTION public.update_board_query_updated_at();
