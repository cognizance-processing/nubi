import { createClient } from '@supabase/supabase-js'

const supabaseUrl = 'https://ienrcbzcbprtawioqinj.supabase.co'
const supabaseAnonKey = 'sb_publishable_AZ_r6qU-DcM9MknJrW49zg_W84IxESK'

export const supabase = createClient(supabaseUrl, supabaseAnonKey)

/**
 * Invoke the board-helper endpoint via Python backend.
 * @param {{ code?: string, user_prompt: string, chat?: { role: 'user'|'assistant', content: string }[], gemini_api_key?: string, context?: 'board'|'exploration', datastore_id?: string, exploration_id?: string }} options
 * @returns {Promise<{ data?: { code?: string, message?: string, error?: string, progress?: string[], test_passed?: boolean, attempts?: number }, error?: Error }>}
 */
export async function invokeBoardHelper(options) {
  const { code = '', user_prompt, chat = [], gemini_api_key, context = 'board', datastore_id, exploration_id } = options
  const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
  
  try {
    const response = await fetch(`${backendUrl}/board-helper`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code,
        user_prompt,
        chat,
        context,
        ...(datastore_id && { datastore_id }),
        ...(exploration_id && { exploration_id }),
        ...(gemini_api_key && { gemini_api_key })
      })
    })
    
    const data = await response.json()
    
    if (!response.ok) {
      return { error: new Error(data.detail || 'AI helper request failed'), data: null }
    }
    
    return { data, error: null }
  } catch (err) {
    return { error: err, data: null }
  }
}

/**
 * Get database schema information.
 * @param {{ datastore_id?: string, connector_id?: string, database?: string, table?: string }} options
 * @returns {Promise<{ data?: any, error?: Error }>}
 */
export async function getSchema(options) {
  const { datastore_id, connector_id, database, table } = options
  const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'
  
  try {
    const response = await fetch(`${backendUrl}/get-schema`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        datastore_id: datastore_id || connector_id,
        connector_id: connector_id || datastore_id,
        database, 
        table 
      })
    })
    
    const data = await response.json()
    
    if (!response.ok) {
      return { error: new Error(data.detail || 'Schema request failed'), data: null }
    }
    
    return { data, error: null }
  } catch (err) {
    return { error: err, data: null }
  }
}

