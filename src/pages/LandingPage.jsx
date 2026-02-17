import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { 
  Sparkles, 
  Code2, 
  Database, 
  MessageSquare, 
  BarChart3, 
  Zap,
  ArrowRight,
  Github,
  CheckCircle2
} from 'lucide-react'

export default function LandingPage() {
  const navigate = useNavigate()
  const { user } = useAuth()

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-indigo-950 to-slate-950">
      {/* Navigation */}
      <nav className="fixed top-0 w-full z-50 bg-slate-950/80 backdrop-blur-lg border-b border-slate-800/50">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <span className="text-2xl font-bold bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">
              Nubi
            </span>
          </div>

          <div className="flex items-center gap-4">
            <a 
              href="https://github.com/yourusername/nubi" 
              target="_blank" 
              rel="noopener noreferrer"
              className="text-slate-400 hover:text-white transition-colors"
            >
              <Github className="w-5 h-5" />
            </a>
            {user ? (
              <button
                onClick={() => navigate('/portal')}
                className="px-6 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-all hover:scale-105"
              >
                Go to Portal
              </button>
            ) : (
              <button
                onClick={() => navigate('/login')}
                className="px-6 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-all hover:scale-105"
              >
                Login
              </button>
            )}
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="pt-32 pb-20 px-6">
        <div className="max-w-7xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-500/10 border border-indigo-500/20 rounded-full text-indigo-400 text-sm mb-8">
            <Sparkles className="w-4 h-4" />
            <span>LLM-First Business Intelligence</span>
          </div>

          <h1 className="text-6xl md:text-7xl font-bold text-white mb-6 leading-tight">
            Ask Questions.
            <br />
            <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
              Get Insights.
            </span>
          </h1>

          <p className="text-xl text-slate-400 max-w-3xl mx-auto mb-12 leading-relaxed">
            Simple yet powerful BI tool that speaks your language. Query data with natural language, 
            Python, or SQL. Build beautiful dashboards without the complexity.
          </p>

          <div className="flex items-center justify-center gap-4">
            {user ? (
              <button
                onClick={() => navigate('/portal')}
                className="group px-8 py-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold text-lg transition-all hover:scale-105 flex items-center gap-2"
              >
                Go to Portal
                <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
              </button>
            ) : (
              <>
                <button
                  onClick={() => navigate('/login')}
                  className="group px-8 py-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold text-lg transition-all hover:scale-105 flex items-center gap-2"
                >
                  Get Started
                  <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                </button>
                <a
                  href="https://github.com/yourusername/nubi"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-8 py-4 bg-slate-800 hover:bg-slate-700 text-white rounded-lg font-semibold text-lg transition-all hover:scale-105 flex items-center gap-2"
                >
                  <Github className="w-5 h-5" />
                  View on GitHub
                </a>
              </>
            )}
          </div>

          {/* Demo Visual */}
          <div className="mt-20 relative">
            <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-transparent to-transparent z-10"></div>
            <div className="bg-slate-900/50 border border-slate-800 rounded-2xl p-8 backdrop-blur-sm">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-3 h-3 rounded-full bg-red-500"></div>
                <div className="w-3 h-3 rounded-full bg-yellow-500"></div>
                <div className="w-3 h-3 rounded-full bg-green-500"></div>
              </div>
              <div className="text-left font-mono text-sm">
                <div className="text-slate-500 mb-2"># Ask in plain English</div>
                <div className="text-indigo-400 mb-4">
                  "Show me revenue by region for Q4 2024"
                </div>
                <div className="text-slate-500 mb-2"># Nubi generates the query</div>
                <div className="text-purple-400">
                  SELECT region, SUM(revenue) as total
                  <br />
                  FROM sales
                  <br />
                  WHERE date BETWEEN '2024-10-01' AND '2024-12-31'
                  <br />
                  GROUP BY region
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-20 px-6 bg-slate-900/30">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-4xl font-bold text-white mb-4">
              Everything you need for data exploration
            </h2>
            <p className="text-xl text-slate-400">
              Powerful features that scale with your needs
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
            {/* Feature 1 */}
            <div className="group p-8 bg-slate-800/50 border border-slate-700/50 rounded-2xl hover:border-indigo-500/50 transition-all hover:scale-105">
              <div className="w-12 h-12 bg-indigo-500/10 rounded-xl flex items-center justify-center mb-4 group-hover:bg-indigo-500/20 transition-colors">
                <MessageSquare className="w-6 h-6 text-indigo-400" />
              </div>
              <h3 className="text-xl font-semibold text-white mb-3">Natural Language Queries</h3>
              <p className="text-slate-400">
                Ask questions in plain English. Nubi translates your intent into optimized SQL automatically.
              </p>
            </div>

            {/* Feature 2 */}
            <div className="group p-8 bg-slate-800/50 border border-slate-700/50 rounded-2xl hover:border-purple-500/50 transition-all hover:scale-105">
              <div className="w-12 h-12 bg-purple-500/10 rounded-xl flex items-center justify-center mb-4 group-hover:bg-purple-500/20 transition-colors">
                <Code2 className="w-6 h-6 text-purple-400" />
              </div>
              <h3 className="text-xl font-semibold text-white mb-3">Python & SQL Native</h3>
              <p className="text-slate-400">
                Full control with Python transformations and SQL queries. Use pandas, numpy, and your favorite libraries.
              </p>
            </div>

            {/* Feature 3 */}
            <div className="group p-8 bg-slate-800/50 border border-slate-700/50 rounded-2xl hover:border-pink-500/50 transition-all hover:scale-105">
              <div className="w-12 h-12 bg-pink-500/10 rounded-xl flex items-center justify-center mb-4 group-hover:bg-pink-500/20 transition-colors">
                <BarChart3 className="w-6 h-6 text-pink-400" />
              </div>
              <h3 className="text-xl font-semibold text-white mb-3">Interactive Dashboards</h3>
              <p className="text-slate-400">
                Drag-and-drop widgets, real-time charts, and KPI cards. Beautiful by default, customizable when needed.
              </p>
            </div>

            {/* Feature 4 */}
            <div className="group p-8 bg-slate-800/50 border border-slate-700/50 rounded-2xl hover:border-cyan-500/50 transition-all hover:scale-105">
              <div className="w-12 h-12 bg-cyan-500/10 rounded-xl flex items-center justify-center mb-4 group-hover:bg-cyan-500/20 transition-colors">
                <Database className="w-6 h-6 text-cyan-400" />
              </div>
              <h3 className="text-xl font-semibold text-white mb-3">Multi-Source Connections</h3>
              <p className="text-slate-400">
                Connect to BigQuery, PostgreSQL, and more. Query across multiple databases in a single flow.
              </p>
            </div>

            {/* Feature 5 */}
            <div className="group p-8 bg-slate-800/50 border border-slate-700/50 rounded-2xl hover:border-green-500/50 transition-all hover:scale-105">
              <div className="w-12 h-12 bg-green-500/10 rounded-xl flex items-center justify-center mb-4 group-hover:bg-green-500/20 transition-colors">
                <Zap className="w-6 h-6 text-green-400" />
              </div>
              <h3 className="text-xl font-semibold text-white mb-3">Data Stitching Engine</h3>
              <p className="text-slate-400">
                Chain SQL queries and Python logic into powerful workflows. Template variables and context passing built-in.
              </p>
            </div>

            {/* Feature 6 */}
            <div className="group p-8 bg-slate-800/50 border border-slate-700/50 rounded-2xl hover:border-orange-500/50 transition-all hover:scale-105">
              <div className="w-12 h-12 bg-orange-500/10 rounded-xl flex items-center justify-center mb-4 group-hover:bg-orange-500/20 transition-colors">
                <Sparkles className="w-6 h-6 text-orange-400" />
              </div>
              <h3 className="text-xl font-semibold text-white mb-3">LLM-Powered Assistant</h3>
              <p className="text-slate-400">
                Conversational analytics powered by Google Gemini. Ask follow-ups, iterate on queries, explore data naturally.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Why Nubi Section */}
      <section className="py-20 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="grid lg:grid-cols-2 gap-16 items-center">
            <div>
              <h2 className="text-4xl font-bold text-white mb-6">
                Why Nubi?
              </h2>
              <p className="text-lg text-slate-400 mb-8">
                Traditional BI tools force you to choose between simplicity and power. 
                Nubi gives you both.
              </p>

              <div className="space-y-4">
                <div className="flex items-start gap-3">
                  <CheckCircle2 className="w-6 h-6 text-green-400 flex-shrink-0 mt-1" />
                  <div>
                    <div className="text-white font-semibold mb-1">Start Simple</div>
                    <div className="text-slate-400">Ask questions in natural language. No SQL required.</div>
                  </div>
                </div>

                <div className="flex items-start gap-3">
                  <CheckCircle2 className="w-6 h-6 text-green-400 flex-shrink-0 mt-1" />
                  <div>
                    <div className="text-white font-semibold mb-1">Scale Up</div>
                    <div className="text-slate-400">Graduate to Python and SQL when you need more control.</div>
                  </div>
                </div>

                <div className="flex items-start gap-3">
                  <CheckCircle2 className="w-6 h-6 text-green-400 flex-shrink-0 mt-1" />
                  <div>
                    <div className="text-white font-semibold mb-1">Beautiful by Default</div>
                    <div className="text-slate-400">Dashboards that look great without endless customization.</div>
                  </div>
                </div>

                <div className="flex items-start gap-3">
                  <CheckCircle2 className="w-6 h-6 text-green-400 flex-shrink-0 mt-1" />
                  <div>
                    <div className="text-white font-semibold mb-1">Developer Friendly</div>
                    <div className="text-slate-400">API-first design. Version control your queries and dashboards.</div>
                  </div>
                </div>
              </div>
            </div>

            <div className="relative">
              <div className="absolute inset-0 bg-gradient-to-r from-indigo-500 to-purple-500 rounded-3xl blur-3xl opacity-20"></div>
              <div className="relative bg-slate-800/50 border border-slate-700 rounded-3xl p-8">
                <div className="space-y-6">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-indigo-500 rounded-xl flex items-center justify-center">
                      <span className="text-2xl">🚀</span>
                    </div>
                    <div>
                      <div className="text-white font-semibold">Quick Setup</div>
                      <div className="text-slate-400 text-sm">Running in under 5 minutes</div>
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-purple-500 rounded-xl flex items-center justify-center">
                      <span className="text-2xl">⚡</span>
                    </div>
                    <div>
                      <div className="text-white font-semibold">Lightning Fast</div>
                      <div className="text-slate-400 text-sm">Optimized queries, instant results</div>
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-pink-500 rounded-xl flex items-center justify-center">
                      <span className="text-2xl">🎨</span>
                    </div>
                    <div>
                      <div className="text-white font-semibold">Modern UI</div>
                      <div className="text-slate-400 text-sm">Built with React, TailwindCSS</div>
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-cyan-500 rounded-xl flex items-center justify-center">
                      <span className="text-2xl">🔓</span>
                    </div>
                    <div>
                      <div className="text-white font-semibold">Open Source</div>
                      <div className="text-slate-400 text-sm">Apache 2.0 License</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-20 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <div className="bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 p-1 rounded-3xl">
            <div className="bg-slate-950 rounded-3xl p-12">
              <h2 className="text-4xl font-bold text-white mb-6">
                Ready to explore your data?
              </h2>
              <p className="text-xl text-slate-400 mb-8">
                Join the next generation of business intelligence.
              </p>
              {user ? (
                <button
                  onClick={() => navigate('/portal')}
                  className="group px-8 py-4 bg-white text-slate-950 rounded-lg font-semibold text-lg transition-all hover:scale-105 flex items-center gap-2 mx-auto"
                >
                  Go to Portal
                  <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                </button>
              ) : (
                <button
                  onClick={() => navigate('/login')}
                  className="group px-8 py-4 bg-white text-slate-950 rounded-lg font-semibold text-lg transition-all hover:scale-105 flex items-center gap-2 mx-auto"
                >
                  Get Started Free
                  <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                </button>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-6 border-t border-slate-800">
        <div className="max-w-7xl mx-auto">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <div className="w-6 h-6 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center">
                <Sparkles className="w-4 h-4 text-white" />
              </div>
              <span className="text-slate-400">
                © 2026 Nubi. Built with ❤️ for data people.
              </span>
            </div>

            <div className="flex items-center gap-6">
              <a href="https://github.com/yourusername/nubi" className="text-slate-400 hover:text-white transition-colors">
                GitHub
              </a>
              <a href="#" className="text-slate-400 hover:text-white transition-colors">
                Documentation
              </a>
              <a href="#" className="text-slate-400 hover:text-white transition-colors">
                Community
              </a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  )
}
