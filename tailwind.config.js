/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                background: {
                    primary: 'rgb(var(--bg-primary) / <alpha-value>)',
                    secondary: 'rgb(var(--bg-secondary) / <alpha-value>)',
                    tertiary: 'rgb(var(--bg-tertiary) / <alpha-value>)',
                    hover: 'rgb(var(--bg-hover) / <alpha-value>)',
                },
                accent: {
                    primary: 'rgb(var(--accent-primary) / <alpha-value>)',
                    secondary: 'rgb(var(--accent-secondary) / <alpha-value>)',
                },
                text: {
                    primary: 'rgb(var(--text-primary) / <alpha-value>)',
                    secondary: 'rgb(var(--text-secondary) / <alpha-value>)',
                    muted: 'rgb(var(--text-muted) / <alpha-value>)',
                },
                border: {
                    primary: 'rgb(var(--border-primary) / <alpha-value>)',
                    secondary: 'rgb(var(--border-secondary) / <alpha-value>)',
                },
                status: {
                    success: 'rgb(var(--success) / <alpha-value>)',
                    warning: 'rgb(var(--warning) / <alpha-value>)',
                    error: 'rgb(var(--error) / <alpha-value>)',
                }
            },
            backgroundImage: {
                'accent-gradient': 'var(--accent-gradient)',
            },
            boxShadow: {
                'glow': 'var(--shadow-glow)',
            }
        },
    },
    plugins: [],
}
