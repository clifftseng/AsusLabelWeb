import React, { useState } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Page1, { AnalysisResult } from './Page1';

function App() {
  const [analysisResults, setAnalysisResults] = useState<AnalysisResult[]>([]);
  const [analysisPath, setAnalysisPath] = useState<string>('');

  return (
    <Router>
      <div className="App">
        <Routes>
          <Route
            path="/"
            element={
              <Page1
                setAnalysisResults={setAnalysisResults}
                analysisResults={analysisResults}
                setAnalysisPath={setAnalysisPath}
                analysisPath={analysisPath}
              />
            }
          />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
