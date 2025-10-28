import React, { useState } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Page1 from './Page1';

interface AnalysisResult {
  id: number;
  filename: string;
  model_name: string;
  voltage: string;
  typ_batt_capacity_wh: string;
  typ_capacity_mah: string;
  rated_capacity_mah: string;
  rated_energy_wh: string;
}

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
