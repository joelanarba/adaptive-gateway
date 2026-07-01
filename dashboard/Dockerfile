FROM node:20-alpine

WORKDIR /app

# Install dependencies if package.json exists. Otherwise, they will be mounted via volume in dev mode.
COPY package.json package-lock.json* ./
RUN if [ -f package.json ]; then npm install; fi

# Copy the rest of the application
COPY . .

# Expose port
EXPOSE 3000

# Start development server
CMD ["npm", "run", "dev"]
