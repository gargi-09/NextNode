import Markdown from "react-markdown";

// react-markdown renders to React elements and ignores raw HTML by default, so
// LLM output (which resume/JD text can influence) can't inject markup or scripts.
const COMPONENTS = {
  h2: props => <h3 className="text-sm font-semibold text-gray-900 dark:text-white mt-4 mb-2 first:mt-0" {...props} />,
  h3: props => <h4 className="text-sm font-semibold text-gray-900 dark:text-white mt-3 mb-1" {...props} />,
  p: props => <p className="text-sm text-gray-600 dark:text-gray-300 mb-2 leading-relaxed" {...props} />,
  ul: props => <ul className="list-disc pl-5 space-y-1 mb-2 text-sm text-gray-600 dark:text-gray-300" {...props} />,
  ol: props => <ol className="list-decimal pl-5 space-y-1 mb-2 text-sm text-gray-600 dark:text-gray-300" {...props} />,
  strong: props => <strong className="font-semibold text-gray-900 dark:text-white" {...props} />,
  a: ({ children }) => <span className="underline">{children}</span>, // no outbound links from LLM text
};

export default function Explanation({ text }) {
  return <Markdown components={COMPONENTS}>{text}</Markdown>;
}
