import { Link } from 'react-router-dom'
import { Sparkles, ArrowLeft } from 'lucide-react'

const lastUpdated = 'February 22, 2026'

const sections = [
  {
    title: '1. Acceptance of Terms',
    content: `By accessing or using Nubi ("the Service"), you agree to be bound by these Terms of Service. If you do not agree to these terms, you may not use the Service. These terms apply to all users, including visitors, registered users, and contributors.`,
  },
  {
    title: '2. Description of Service',
    content: `Nubi is an open-source, LLM-first business intelligence platform that allows users to query databases using natural language, SQL, and Python. The Service includes the web application, API, and any associated documentation. Nubi is provided under the Apache 2.0 open-source license for self-hosted deployments.`,
  },
  {
    title: '3. User Accounts',
    content: `To access certain features of the Service, you must create an account. You are responsible for maintaining the confidentiality of your account credentials and for all activities that occur under your account. You agree to notify us immediately of any unauthorized use of your account. We reserve the right to suspend or terminate accounts that violate these terms.`,
  },
  {
    title: '4. Your Data',
    content: `You retain full ownership of all data you process through Nubi. We do not claim any intellectual property rights over your data. When self-hosting Nubi, your data never leaves your infrastructure. For any hosted service, we process your data only to provide the Service and do not sell, share, or use your data for advertising purposes.`,
  },
  {
    title: '5. Acceptable Use',
    content: `You agree not to use the Service to:
• Violate any applicable law or regulation
• Infringe upon the rights of others
• Transmit malicious code or attempt to compromise system security
• Interfere with or disrupt the Service or its infrastructure
• Attempt to gain unauthorized access to other users' accounts or data
• Use automated means to scrape or extract data from the Service beyond normal API usage`,
  },
  {
    title: '6. Intellectual Property',
    content: `Nubi's source code is licensed under the Apache 2.0 License. The Nubi name, logo, and branding are trademarks and may not be used without permission. Contributions to the open-source project are subject to the project's Contributor License Agreement.`,
  },
  {
    title: '7. Third-Party Services',
    content: `Nubi integrates with third-party services including database providers (BigQuery, PostgreSQL) and AI providers (Google Gemini). Your use of these services is subject to their respective terms and conditions. We are not responsible for the availability, accuracy, or practices of third-party services.`,
  },
  {
    title: '8. Disclaimers',
    content: `The Service is provided "as is" and "as available" without warranties of any kind, either express or implied. We do not guarantee that the Service will be uninterrupted, secure, or error-free. AI-generated queries and insights should be reviewed before being used for critical business decisions. We are not liable for any inaccuracies in LLM-generated outputs.`,
  },
  {
    title: '9. Limitation of Liability',
    content: `To the maximum extent permitted by law, Nubi and its maintainers shall not be liable for any indirect, incidental, special, consequential, or punitive damages, or any loss of profits or revenues, whether incurred directly or indirectly, arising from your use of the Service.`,
  },
  {
    title: '10. Changes to Terms',
    content: `We reserve the right to modify these terms at any time. Changes will be posted on this page with an updated revision date. Continued use of the Service after changes constitutes acceptance of the updated terms. For significant changes, we will make reasonable efforts to notify users.`,
  },
  {
    title: '11. Contact',
    content: `If you have questions about these Terms of Service, please open an issue on our GitHub repository or contact the project maintainers.`,
  },
]

export default function TermsPage() {
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

        <h1 className="text-3xl font-bold text-white mb-2">Terms of Service</h1>
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
            <Link to="/privacy" className="text-slate-500 hover:text-white transition-colors">Privacy Policy</Link>
            <Link to="/docs" className="text-slate-500 hover:text-white transition-colors">Documentation</Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
