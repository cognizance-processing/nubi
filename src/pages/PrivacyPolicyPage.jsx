import { Link } from 'react-router-dom'
import { Sparkles, ArrowLeft } from 'lucide-react'

const lastUpdated = 'February 22, 2026'

const sections = [
  {
    title: '1. Introduction',
    content: `This Privacy Policy explains how Nubi ("we", "us", or "our") collects, uses, and protects your information when you use our platform. We are committed to safeguarding your privacy and ensuring transparency about our data practices. Nubi is designed as a self-hosted platform, meaning your data stays on your infrastructure by default.`,
  },
  {
    title: '2. Information We Collect',
    content: `When you use Nubi, we may collect the following types of information:

Account Information — When you create an account, we collect your email address and authentication credentials (managed through Supabase Auth).

Usage Data — We collect anonymized usage analytics to improve the product, including pages visited, features used, and general interaction patterns. This data does not include query contents or database records.

Database Metadata — When you connect a data source, Nubi reads schema information (table names, column names, data types) to enable natural language query generation. This metadata is stored locally within your deployment.

Query History — Your queries and their results are stored within your Nubi deployment for history and caching purposes. In self-hosted mode, this data never leaves your servers.`,
  },
  {
    title: '3. How We Use Your Information',
    content: `We use collected information to:
• Provide and maintain the Service
• Authenticate users and manage sessions
• Enable natural language to SQL translation
• Improve the product through anonymized analytics
• Communicate important updates about the Service
• Respond to support requests

We do not sell your personal information or data to third parties.`,
  },
  {
    title: '4. Third-Party Services',
    content: `Nubi integrates with third-party services that have their own privacy policies:

Google Gemini API — When you use natural language queries, your question and relevant schema context are sent to Google's Gemini API for processing. Google's AI data usage policies apply. No raw data from your database is sent — only the question and schema metadata needed to generate a query.

Supabase — Used for authentication and metadata storage. Subject to Supabase's privacy policy.

Database Providers — Connections to your databases (BigQuery, PostgreSQL, etc.) are direct from your Nubi deployment. Credentials are encrypted at rest.`,
  },
  {
    title: '5. Data Storage & Security',
    content: `We implement appropriate security measures to protect your information:

• All data in transit is encrypted using TLS/HTTPS
• Database credentials are encrypted at rest
• Self-hosted deployments keep all data within your infrastructure
• Authentication is handled through industry-standard protocols (Supabase Auth)
• We follow the principle of least privilege for all data access

We retain your account information for as long as your account is active. You can request deletion of your account and associated data at any time.`,
  },
  {
    title: '6. Self-Hosted Deployments',
    content: `When you self-host Nubi, your data stays entirely within your infrastructure. The only external communication is:
• LLM API calls (schema metadata and natural language questions, not raw data)
• Optional anonymized telemetry (can be disabled)

No database records, query results, or business data are transmitted to Nubi or any third party in self-hosted mode.`,
  },
  {
    title: '7. Your Rights',
    content: `You have the right to:
• Access the personal information we hold about you
• Request correction of inaccurate information
• Request deletion of your account and data
• Export your data in a portable format
• Opt out of non-essential data collection
• Withdraw consent at any time

To exercise these rights, contact us through our GitHub repository or email the project maintainers.`,
  },
  {
    title: '8. Cookies',
    content: `Nubi uses only essential cookies required for authentication and session management. We do not use advertising cookies or third-party tracking cookies. Session tokens are stored securely and expire after a reasonable period of inactivity.`,
  },
  {
    title: '9. Children\'s Privacy',
    content: `Nubi is not intended for use by children under the age of 13. We do not knowingly collect personal information from children. If we become aware that a child under 13 has provided personal information, we will take steps to delete it.`,
  },
  {
    title: '10. Changes to This Policy',
    content: `We may update this Privacy Policy from time to time. Changes will be posted on this page with an updated revision date. We encourage you to review this policy periodically. For significant changes, we will make reasonable efforts to notify users through the application or via email.`,
  },
  {
    title: '11. Contact Us',
    content: `If you have questions or concerns about this Privacy Policy, please open an issue on our GitHub repository or contact the project maintainers directly.`,
  },
]

export default function PrivacyPolicyPage() {
  return (
    <div className="min-h-screen bg-[#050816] text-white">
      <nav className="fixed top-0 w-full z-50 bg-[#050816]/90 backdrop-blur-xl border-b border-white/[0.06]">
        <div className="max-w-4xl mx-auto px-6 h-14 flex items-center">
          <Link to="/" className="flex items-center gap-2.5">
            <div className="w-7 h-7 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center">
              <Sparkles className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-lg font-bold">Nubi</span>
          </Link>
        </div>
      </nav>

      <main className="max-w-3xl mx-auto px-6 pt-28 pb-20">
        <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-white transition-colors mb-8">
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to home
        </Link>

        <h1 className="text-3xl font-bold text-white mb-2">Privacy Policy</h1>
        <p className="text-sm text-slate-500 mb-12">Last updated: {lastUpdated}</p>

        <div className="space-y-10">
          {sections.map((section) => (
            <div key={section.title}>
              <h2 className="text-lg font-semibold text-white mb-3">{section.title}</h2>
              <div className="text-slate-400 text-[0.95rem] leading-relaxed whitespace-pre-line">
                {section.content}
              </div>
            </div>
          ))}
        </div>
      </main>

      <footer className="border-t border-white/[0.04] bg-[#030712]">
        <div className="max-w-3xl mx-auto px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-4">
          <span className="text-sm text-slate-600">&copy; {new Date().getFullYear()} Nubi</span>
          <div className="flex items-center gap-6 text-sm">
            <Link to="/terms" className="text-slate-500 hover:text-white transition-colors">Terms of Service</Link>
            <Link to="/docs" className="text-slate-500 hover:text-white transition-colors">Documentation</Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
