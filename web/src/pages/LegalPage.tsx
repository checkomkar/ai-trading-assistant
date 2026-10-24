import type { ReactNode } from "react";
import { brand } from "../brand";

/** The wording lives here so the sign-up screen, footer and legal pages stay in step. */
export const RISK_WARNING =
  "Trading in futures and options carries a high risk of loss, and most people who buy options lose money. " +
  `${brand.name} is a research and paper-trading tool. It does not place real orders, it is not investment advice, and it is ` +
  "not responsible for any trade you make anywhere. Past or simulated results do not predict future results.";

const UPDATED = "21 October 2026";
const CONTACT = `support@${brand.domain}`;

function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 space-y-5 p-4 text-sm leading-relaxed text-ink-200 sm:p-6">
      <header>
        <h1 className="text-xl font-semibold text-ink-100">{title}</h1>
        <p className="mt-1 text-xs text-ink-400">Last updated {UPDATED}</p>
        <p className="mt-3 rounded-md border border-sim/40 bg-sim/10 px-3 py-2 text-xs text-sim">
          Draft text. Have a lawyer qualified in Indian law review it, and fill in the operator's legal name and address, before
          you charge anyone.
        </p>
      </header>
      {children}
      <nav className="flex gap-4 border-t border-ink-800 pt-4 text-xs text-ink-400">
        <a className="underline hover:text-ink-100" href="#/terms">Terms</a>
        <a className="underline hover:text-ink-100" href="#/privacy">Privacy</a>
        <a className="underline hover:text-ink-100" href="#/">Back to the app</a>
      </nav>
    </main>
  );
}

function S({ h, children }: { h: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-ink-100">{h}</h2>
      {children}
    </section>
  );
}

const ul = "list-disc space-y-1 pl-5";

export function TermsPage() {
  return (
    <Shell title="Terms and Conditions">
      <p className="rounded-md border border-down/40 bg-down/10 px-3 py-2 font-medium text-down" role="note">{RISK_WARNING}</p>
      <S h="1. Who we are and what you accept">
        <p>
          {brand.name} ("we", "us") is operated by [operator legal name, address]. By creating an account or using the service you
          agree to these terms and to the <a className="underline" href="#/privacy">Privacy Policy</a>. If you do not agree, do not use it.
          You must be at least 18 and legally able to enter a contract.
        </p>
      </S>
      <S h="2. What the service is">
        <p>
          {brand.name} provides market charts, option-chain data, strategy writing and backtesting tools, an AI assistant, and
          paper trading with virtual money. It runs strategies on live prices and may display the order a real broker would
          receive ("shadow mode"), but <strong>it does not send orders to any broker or exchange</strong>. Any trade you place
          is placed by you, with your own broker, outside this service.
        </p>
      </S>
      <S h="3. Not advice, no responsibility for trades">
        <ul className={ul}>
          <li>Nothing here is investment, financial, tax or legal advice, a recommendation, or a research report. We are not a SEBI-registered investment adviser or research analyst unless we say so in writing.</li>
          <li>Strategies, signals, backtests, paper results and AI output are for learning and research. Backtests and paper results use past or simulated data, ignore real-world slippage and liquidity, and routinely overstate what a live account would earn.</li>
          <li>AI output can be wrong, incomplete or out of date. You must read and check every strategy before you rely on it.</li>
          <li><strong>You alone decide whether to trade and are solely responsible for every trade, profit, loss, tax and compliance duty.</strong> To the fullest extent the law allows, we are not liable for any trading loss or lost profit, whether or not it followed something you saw here.</li>
          <li>Options can expire worthless. Do not trade money you cannot afford to lose.</li>
        </ul>
      </S>
      <S h="4. Your account">
        <p>
          Keep your password private and tell us if you think someone else has used your account. You are responsible for activity
          under it. One person, one account: creating several accounts to get extra free trials may get them closed.
        </p>
      </S>
      <S h="5. Plans, usage limits and payment">
        <ul className={ul}>
          <li>The free trial and each paid plan include a stated number of AI generations, messages and other items, shown on the Pricing page. Limits can change for future periods; we will not reduce what you have already paid for in the current period.</li>
          <li>Paid plans renew monthly through our payment provider (Razorpay) until you cancel. Cancelling stops the next renewal; access continues to the end of the paid period.</li>
          <li>Prices include GST where it applies. Except where the law requires it, payments are not refundable once a billing period has started. If we withdraw a paid feature, we will refund the unused part of the period.</li>
          <li>We do not store your card details; the payment provider handles them.</li>
        </ul>
      </S>
      <S h="6. Broker keys">
        <p>
          If you save API keys from a broker, you authorise us to store them encrypted and to use them only to read market data for
          your account. You are responsible for the permissions you grant at the broker, for keeping the keys secret, and for
          following the broker's terms. Create keys with the narrowest permissions available, and delete them from the Brokers page or at
          the broker if you stop using the service.
        </p>
      </S>
      <S h="7. Acceptable use">
        <p>
          Do not misuse the service: no scraping or reselling market data, no attempts to break, overload or bypass limits, no unlawful use,
          no market manipulation, and no sharing your login. Market data belongs to the exchange and our data suppliers and is for your
          own display only.
        </p>
      </S>
      <S h="8. Data, availability and changes">
        <p>
          Prices and data may be delayed, wrong or missing, and the service may be interrupted. It is provided "as is" and "as available",
          without warranties of accuracy, uptime or fitness for any purpose. We may change or stop features. This is an early product.
        </p>
      </S>
      <S h="9. Liability">
        <p>
          To the fullest extent permitted by law, our total liability to you for anything connected with the service is limited to the
          amount you paid us in the three months before the claim, and we are not liable for indirect or consequential loss, lost
          profits, or trading losses. Nothing here excludes liability that cannot be excluded by law.
        </p>
      </S>
      <S h="10. Ending the relationship">
        <p>
          You may stop using the service and ask us to delete your account at any time. We may suspend or close an account that breaks these
          terms or puts the service at risk.
        </p>
      </S>
      <S h="11. Law and disputes">
        <p>
          These terms are governed by the laws of India. The courts at [city], India have exclusive jurisdiction, subject to any
          mandatory consumer rights. Questions: <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>.
        </p>
      </S>
    </Shell>
  );
}

export function PrivacyPage() {
  return (
    <Shell title="Privacy Policy">
      <p>
        This explains what {brand.name} collects, why, and your choices, in line with India's Digital Personal Data Protection Act, 2023.
        The operator ("data fiduciary") is [operator legal name, address]. Contact: <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>.
      </p>
      <S h="What we collect">
        <ul className={ul}>
          <li><strong>Account:</strong> name, email address, and a one-way hash of your password (we cannot read your password).</li>
          <li><strong>Your work:</strong> strategies, backtests, assistant conversations, files you upload, and notes the assistant keeps for you.</li>
          <li><strong>Voice:</strong> if you use the microphone, the audio is sent to a speech-to-text provider to produce text. We do not keep the audio.</li>
          <li><strong>Broker keys:</strong> only if you add them. They are stored encrypted, are never shown back, and are used only for your account.</li>
          <li><strong>Usage and billing:</strong> counts and token usage of AI calls, plan and subscription status, and payment references from Razorpay. We never see your card number.</li>
          <li><strong>Technical:</strong> a session cookie that keeps you signed in, and server logs (IP address, time, request) for security and fixing faults.</li>
          <li><strong>Analytics, only if you accept:</strong> which pages you open and which buttons on the home page you use, through Google Analytics. Strategies, results and account details are not sent.</li>
        </ul>
      </S>
      <S h="Why we use it">
        <p>
          To run the service you asked for (sign-in, saving your work, AI features, billing), to enforce plan limits, to keep it secure, and to
          meet legal duties. We do not sell your personal data and we do not show advertising.
        </p>
      </S>
      <S h="Who else handles it">
        <ul className={ul}>
          <li><strong>AI model providers</strong> (for example Anthropic, Groq, OpenRouter): your prompts, the strategy text and any file text you send to the assistant are processed by them to produce an answer.</li>
          <li><strong>Speech-to-text provider</strong> (Deepgram): voice audio, when you use the microphone.</li>
          <li><strong>Payment provider</strong> (Razorpay): payment details and subscription status.</li>
          <li><strong>Google Analytics</strong> (Google): page views and a device identifier in a cookie, only after you accept analytics cookies.</li>
          <li><strong>Hosting and database providers</strong> that store the data for us.</li>
          <li>Authorities, where the law requires it.</li>
        </ul>
        <p>These providers may process data outside India. Do not put anything into the assistant that you do not want them to receive.</p>
      </S>
      <S h="Cookies">
        <p>
          One essential cookie keeps you signed in. Google Analytics cookies are set only if you accept them in the banner; declining changes nothing
          else. To change your choice, clear this site's data in your browser and you will be asked again. We do not use advertising cookies.
        </p>
      </S>
      <S h="How long we keep it and how we protect it">
        <p>
          We keep your data while your account is open and delete it within a reasonable time after you ask us to, except records we must keep by law
          (for example invoices). Passwords are hashed, broker keys are encrypted, and access to your data is limited to your own account. No system is
          perfectly secure; if a breach affects you we will tell you as the law requires.
        </p>
      </S>
      <S h="Your rights">
        <p>
          You may ask to see, correct, export or delete your personal data, withdraw consent, or complain about how it is handled by writing to{" "}
          <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>. If we do not resolve it, you may complain to the Data Protection Board of India.
          The service is for adults only.
        </p>
      </S>
      <S h="Changes">
        <p>If we change this policy in a way that matters, we will tell you in the app or by email before it takes effect.</p>
      </S>
    </Shell>
  );
}
